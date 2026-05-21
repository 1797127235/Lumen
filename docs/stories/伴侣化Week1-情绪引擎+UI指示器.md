# Story: Lumen 伴侣化 Week 1 — 情绪引擎 + MoodStrip UI

**Status:** Ready for implementation
**Branch:** master
**设计依据:** `docs/liu-master-design-20260521-093458.md`（APPROVED）
**测试计划:** `~/.gstack/projects/questionliuxinyu-career-os/liu-master-test-plan-*.md`

---

## 目标

让 Lumen 有一个持久化的情绪状态（5 种），在 Chat 页 header 展示，随每次对话结束后异步更新。Week 1 **不做**思考引擎、推送、电量模型——只做情绪推断 + UI。

**验证门（Week 1 结束时）：** 使用 3 天后，`lumen_state.mood` 有真实变化（不是一直 `calm`）。如果没有变化，停在这里重新设计推断逻辑，不进 Week 2。

---

## 涉及文件

| 文件 | 操作 |
|---|---|
| `core/migrations.py` | 新增 3 张表 DDL |
| `lib/companion/` | 新建目录 |
| `lib/companion/__init__.py` | 空文件 |
| `lib/companion/models.py` | Lumen 伴侣相关 SQLAlchemy 模型 |
| `lib/companion/mood_inference.py` | 情绪推断函数 |
| `lib/companion/presence.py` | PresenceStore（last_user_at 更新） |
| `lib/chat/persistence.py` | 2 处修改（presence 写入 + mood 任务） |
| `server/routes/companion.py` | 新路由：GET /api/companion/mood |
| `main.py` | 注册新路由 |
| `src/lib/api/companion.ts` | 前端 API client |
| `src/components/MoodStrip.tsx` | 新组件 |
| `src/pages/Chat.tsx` | 插入 MoodStrip |

---

## 1. 数据库迁移（`core/migrations.py`）

在 `migrate_sqlite()` 函数的 SQL 列表末尾追加以下语句（保持现有幂等写法，用 `CREATE TABLE IF NOT EXISTS` 和 `try/except`）：

```python
# ── Lumen 伴侣系统 ──
"""CREATE TABLE IF NOT EXISTS lumen_thoughts (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'demo_user',
    content TEXT NOT NULL,
    source_event_ids TEXT,
    judge_score REAL,
    judge_veto TEXT,
    duplicate INTEGER DEFAULT 0,
    mood TEXT CHECK(mood IN ('calm','curious','tender','reflective','energized')),
    sent_at DATETIME,
    error_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)""",
"CREATE INDEX IF NOT EXISTS ix_lumen_thoughts_user ON lumen_thoughts (user_id)",
"CREATE INDEX IF NOT EXISTS ix_lumen_thoughts_sent ON lumen_thoughts (user_id, sent_at)",

"""CREATE TABLE IF NOT EXISTS lumen_state (
    user_id TEXT NOT NULL DEFAULT 'demo_user',
    mood TEXT NOT NULL DEFAULT 'calm'
        CHECK(mood IN ('calm','curious','tender','reflective','energized')),
    mood_intensity REAL DEFAULT 0.5,
    pending_mood TEXT
        CHECK(pending_mood IS NULL OR pending_mood IN ('calm','curious','tender','reflective','energized')),
    pending_count INTEGER DEFAULT 0,
    derived_from TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id)
)""",
"INSERT OR IGNORE INTO lumen_state (user_id, mood) VALUES ('demo_user', 'calm')",

"""CREATE TABLE IF NOT EXISTS lumen_presence (
    user_id TEXT NOT NULL DEFAULT 'demo_user',
    last_user_at DATETIME,
    last_proactive_at DATETIME,
    proactive_sent_24h INTEGER DEFAULT 0,
    followup_due_at DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id)
)""",
"INSERT OR IGNORE INTO lumen_presence (user_id) VALUES ('demo_user')",
```

**注意：** 整个追加放在现有 SQL 列表的 `]` 之前，每条语句用相同的 `try/except` 包裹（已有 DDL 全部如此处理）。

---

## 2. SQLAlchemy 模型（`lib/companion/models.py`）

新建文件，参考 `lib/chat/models.py` 的写法：

```python
"""Lumen 伴侣系统 — SQLAlchemy ORM 模型"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from core.db import Base


class LumenState(Base):
    __tablename__ = "lumen_state"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, default="demo_user")
    mood: Mapped[str] = mapped_column(String, default="calm")
    mood_intensity: Mapped[float] = mapped_column(Float, default=0.5)
    pending_mood: Mapped[str | None] = mapped_column(String, nullable=True)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)
    derived_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class LumenPresence(Base):
    __tablename__ = "lumen_presence"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, default="demo_user")
    last_user_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_proactive_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    proactive_sent_24h: Mapped[int] = mapped_column(Integer, default=0)
    followup_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class LumenThought(Base):
    __tablename__ = "lumen_thoughts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, default="demo_user")
    content: Mapped[str] = mapped_column(Text)
    source_event_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    judge_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_veto: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate: Mapped[int] = mapped_column(Integer, default=0)
    mood: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
```

---

## 3. 情绪推断（`lib/companion/mood_inference.py`）

**核心原则：** 只读互动元数据，不读对话文本，不读用户情绪。推断的是 Lumen 陪伴这段关系的体验，不是镜像用户情绪。

```python
"""情绪状态推断 — 基于互动元数据，不读对话内容"""
from __future__ import annotations
import json
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from shared.logging import get_logger

logger = get_logger(__name__)

MoodType = Literal["calm", "curious", "tender", "reflective", "energized"]

# 推断规则（优先级从高到低）：
# energized:  消息轮次 >= 10 且 间隔 <= 5 分钟
# curious:    存在 "discovery" / "question" 类型的 GrowthEvent >= 1 条（近 5 次对话）
# tender:     距上次对话 >= 2 天（重逢），或用户轮次少（<= 2）但对话间隔短
# reflective: 消息轮次在 4-9 之间，且 GrowthEvent 包含 "reflection"/"struggle" 类型
# calm:       默认，以上都不满足

async def _get_interaction_metadata(db: AsyncSession, user_id: str) -> dict:
    """从最近 5 次对话读取互动元数据（不读消息文本）"""
    try:
        # 最近 5 次对话的消息数、时间间隔
        result = await db.execute(text("""
            SELECT message_count, last_message_at, created_at
            FROM conversations
            WHERE user_id = :user_id
            ORDER BY last_message_at DESC
            LIMIT 5
        """), {"user_id": user_id})
        rows = result.fetchall()

        if not rows:
            return {}

        msg_count = rows[0][0] or 0
        last_msg_at = rows[0][1]
        now = datetime.now(UTC)

        # 间隔分钟数（与最近一次对话）
        gap_minutes = 0
        if last_msg_at:
            if isinstance(last_msg_at, str):
                from datetime import datetime as dt
                last_msg_at = dt.fromisoformat(last_msg_at.replace("Z", "+00:00"))
            if last_msg_at.tzinfo is None:
                last_msg_at = last_msg_at.replace(tzinfo=UTC)
            gap_minutes = (now - last_msg_at).total_seconds() / 60

        # 距上次对话的天数（取最近两条对话的时间差）
        days_since_last = gap_minutes / 1440  # 转换为天

        # 近 5 次对话的 GrowthEvent 类型分布
        ge_result = await db.execute(text("""
            SELECT event_type FROM growth_events
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT 20
        """), {"user_id": user_id})
        event_types = [r[0] for r in ge_result.fetchall()]

        return {
            "msg_count": msg_count,
            "gap_minutes": gap_minutes,
            "days_since_last": days_since_last,
            "event_types": event_types,
        }
    except Exception as e:
        logger.warning("互动元数据读取失败", error=str(e))
        return {}


def _infer_mood(metadata: dict) -> tuple[MoodType, float, list[str]]:
    """
    返回 (mood, intensity, derived_from_items)
    derived_from_items 是调试用字符串列表
    """
    if not metadata:
        return "calm", 0.3, ["no_data:cold_start"]

    msg_count: int = metadata.get("msg_count", 0)
    gap_minutes: float = metadata.get("gap_minutes", 0)
    days_since_last: float = metadata.get("days_since_last", 0)
    event_types: list[str] = metadata.get("event_types", [])

    derived: list[str] = [
        f"msg_count:{msg_count}",
        f"gap_min:{round(gap_minutes, 1)}",
        f"days_since:{round(days_since_last, 1)}",
    ]

    # energized: 高频快节奏
    if msg_count >= 10 and gap_minutes <= 5:
        derived.append("inferred:energized")
        return "energized", 0.8, derived

    # tender: 重逢（2天以上没聊）
    if days_since_last >= 2:
        derived.append("inferred:tender(reunion)")
        return "tender", 0.7, derived

    # curious: 有探索性/发现类事件
    discovery_events = [et for et in event_types
                        if any(k in et.lower() for k in ("discovery", "question", "learn", "explore", "新"))]
    if len(discovery_events) >= 1:
        derived.append(f"inferred:curious(events:{len(discovery_events)})")
        return "curious", 0.6, derived

    # reflective: 中等深度对话，包含反思类事件
    reflective_events = [et for et in event_types
                         if any(k in et.lower() for k in ("reflection", "struggle", "concern", "困", "迷"))]
    if 4 <= msg_count <= 9 or len(reflective_events) >= 1:
        derived.append(f"inferred:reflective(msg:{msg_count},events:{len(reflective_events)})")
        return "reflective", 0.5, derived

    derived.append("inferred:calm(default)")
    return "calm", 0.4, derived


async def update_mood_state(db_session_factory, user_id: str) -> None:
    """
    对话结束后异步调用。
    读取互动元数据 → 推断新情绪 → 更新 lumen_state（带切换防抖：连续 2 次相同新情绪才切换）
    """
    from core.db import get_async_session_maker
    from lib.companion.models import LumenState

    try:
        async with get_async_session_maker()() as db:
            # 读取元数据
            metadata = await _get_interaction_metadata(db, user_id)
            new_mood, intensity, derived_items = _infer_mood(metadata)
            derived_json = json.dumps(derived_items, ensure_ascii=False)

            # 读取当前状态
            state = await db.get(LumenState, user_id)
            if state is None:
                state = LumenState(user_id=user_id)
                db.add(state)

            current_mood = state.mood

            if new_mood == current_mood:
                # 情绪未变，重置候选
                state.pending_mood = None
                state.pending_count = 0
            elif new_mood == state.pending_mood:
                # 候选情绪连续出现
                state.pending_count = (state.pending_count or 0) + 1
                if state.pending_count >= 2:
                    # 切换
                    state.mood = new_mood
                    state.mood_intensity = intensity
                    state.pending_mood = None
                    state.pending_count = 0
                    logger.info("情绪切换", user_id=user_id, old=current_mood, new=new_mood)
            else:
                # 新候选
                state.pending_mood = new_mood
                state.pending_count = 1

            state.derived_from = derived_json
            await db.commit()

    except Exception as e:
        logger.warning("情绪状态更新失败", error=str(e), user_id=user_id)
```

---

## 4. PresenceStore（`lib/companion/presence.py`）

```python
"""PresenceStore — 追踪用户消息时间和推送时间"""
from __future__ import annotations
from datetime import UTC, datetime
from sqlalchemy.ext.asyncio import AsyncSession
from shared.logging import get_logger

logger = get_logger(__name__)


async def record_user_message(db: AsyncSession, user_id: str) -> None:
    """用户发消息时，在同一 DB session 中更新 last_user_at（由 save_user_message 调用）"""
    try:
        from sqlalchemy import text
        await db.execute(text("""
            INSERT INTO lumen_presence (user_id, last_user_at, updated_at)
            VALUES (:uid, :now, :now)
            ON CONFLICT(user_id) DO UPDATE SET
                last_user_at = :now,
                updated_at = :now
        """), {"uid": user_id, "now": datetime.now(UTC)})
        # 注意：不在此 commit，由调用方（save_user_message）统一 commit
    except Exception as e:
        logger.warning("presence 更新失败（不阻断用户消息）", error=str(e))
```

---

## 5. 修改 `lib/chat/persistence.py`

### 5a. `save_user_message()` — 追加 presence 更新

在 `db.add(msg)` 之后、`await db.commit()` 之前加入 presence 更新：

```python
async def save_user_message(db: AsyncSession, conv, user_input: str) -> Message | None:
    """保存用户消息"""
    msg = Message(
        conversation_id=conv.conversation_id,
        role="user",
        content=user_input,
        intent="consultation",
    )
    db.add(msg)
    conv.message_count = (conv.message_count or 0) + 1
    conv.last_message_at = datetime.now(UTC)

    # ── 新增：presence 记录（与用户消息同一 transaction）──
    try:
        from lib.companion.presence import record_user_message
        # conv.user_id 需要从 conv 对象获取；如果 conv 没有 user_id 属性，
        # 传入字符串 "demo_user" 作为默认值
        _uid = getattr(conv, "user_id", "demo_user") or "demo_user"
        await record_user_message(db, _uid)
    except Exception:
        pass  # presence 失败不阻断消息保存

    try:
        await db.commit()
        await db.refresh(msg)
        return msg
    except Exception:
        logger.exception("保存用户消息失败", conversation_id=conv.conversation_id)
        await db.rollback()
        return None
```

### 5b. `persist_turn()` — 追加情绪推断后台任务

在函数末尾 `return True` 之前，紧跟 `background_memory_review` 任务之后加入：

```python
    # ── 新增：情绪推断（对话结束后异步更新）──
    try:
        from lib.companion.mood_inference import update_mood_state
        from core.db import get_async_session_maker
        mood_task = asyncio.create_task(
            update_mood_state(get_async_session_maker, user_id),
            name=f"mood-inference-{conv.conversation_id[:8]}"
        )
        mood_task.add_done_callback(_log_task_error)
    except Exception:
        pass

    return True
```

**注意：** `user_id` 是 `persist_turn()` 函数签名中已有的参数，直接使用即可。

---

## 6. API 路由（`server/routes/companion.py`）

新建文件：

```python
"""Lumen 伴侣系统 API"""
from __future__ import annotations
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from core.db import get_db
from shared.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/companion", tags=["companion"])

_DEFAULT_USER = "demo_user"


class MoodResponse(BaseModel):
    mood: str
    mood_intensity: float
    updated_at: str | None


@router.get("/mood", response_model=MoodResponse)
async def get_current_mood(
    user_id: str = _DEFAULT_USER,
    db: AsyncSession = Depends(get_db),
) -> MoodResponse:
    """获取 Lumen 当前情绪状态"""
    try:
        from sqlalchemy import text
        result = await db.execute(
            text("SELECT mood, mood_intensity, updated_at FROM lumen_state WHERE user_id = :uid"),
            {"uid": user_id},
        )
        row = result.fetchone()
        if row is None:
            return MoodResponse(mood="calm", mood_intensity=0.4, updated_at=None)
        return MoodResponse(
            mood=row[0] or "calm",
            mood_intensity=row[1] or 0.4,
            updated_at=str(row[2]) if row[2] else None,
        )
    except Exception as e:
        logger.warning("获取情绪状态失败", error=str(e))
        return MoodResponse(mood="calm", mood_intensity=0.4, updated_at=None)
```

---

## 7. 注册路由（`main.py`）

在现有路由注册区域加入（参考其他路由的写法）：

```python
from server.routes.companion import router as companion_router
# ...（其他 import）...

app.include_router(companion_router, prefix="/api")
```

---

## 8. 前端 API Client（`src/lib/api/companion.ts`）

新建文件，参考 `src/lib/api/memory.ts` 的写法：

```typescript
import { http } from "./core";

export type MoodState = {
  mood: "calm" | "curious" | "tender" | "reflective" | "energized";
  mood_intensity: number;
  updated_at: string | null;
};

export function getCurrentMood(): Promise<MoodState> {
  return http<MoodState>("/api/companion/mood");
}
```

---

## 9. MoodStrip 组件（`src/components/MoodStrip.tsx`）

新建文件。5 种状态 + 5 种动画 + 加载/错误/过渡状态：

```tsx
import { useEffect, useState, useRef } from 'react'
import { getCurrentMood, type MoodState } from '../lib/api/companion'

// 情绪配置
const MOOD_CONFIG: Record<
  MoodState['mood'],
  { label: string; icon: string; color: string; animation: string }
> = {
  calm:        { label: '平静',  icon: '🌿', color: 'text-emerald-500', animation: 'animate-breathe' },
  curious:     { label: '好奇',  icon: '✨', color: 'text-amber-500',   animation: 'animate-pulse-gentle' },
  tender:      { label: '温柔',  icon: '💙', color: 'text-blue-400',    animation: 'animate-heartbeat' },
  reflective:  { label: '沉思',  icon: '🤔', color: 'text-violet-400',  animation: 'animate-orbit' },
  energized:   { label: '活跃',  icon: '🌤', color: 'text-orange-400',  animation: 'animate-bounce-soft' },
}

type StripState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: MoodState; transitioning: boolean }
  | { kind: 'error' }

export default function MoodStrip() {
  const [state, setState] = useState<StripState>({ kind: 'loading' })
  const prevMood = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false

    getCurrentMood()
      .then((data) => {
        if (cancelled) return
        const isTransitioning = prevMood.current !== null && prevMood.current !== data.mood
        setState({ kind: 'ready', data, transitioning: isTransitioning })
        if (isTransitioning) {
          // 600ms 过渡后关闭过渡标志
          setTimeout(() => {
            if (!cancelled) {
              setState(s => s.kind === 'ready' ? { ...s, transitioning: false } : s)
            }
          }, 600)
        }
        prevMood.current = data.mood
      })
      .catch(() => {
        if (cancelled) return
        setState({ kind: 'error' })
      })

    // 每 5 分钟轮询一次
    const interval = setInterval(() => {
      getCurrentMood()
        .then((data) => {
          if (cancelled) return
          const isTransitioning = prevMood.current !== null && prevMood.current !== data.mood
          setState({ kind: 'ready', data, transitioning: isTransitioning })
          if (isTransitioning) {
            setTimeout(() => {
              if (!cancelled) {
                setState(s => s.kind === 'ready' ? { ...s, transitioning: false } : s)
              }
            }, 600)
          }
          prevMood.current = data.mood
        })
        .catch(() => { /* 轮询失败静默处理，保留上次状态 */ })
    }, 5 * 60 * 1000)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  // 错误或加载中：隐藏（不展示破损 UI）
  if (state.kind === 'error') return null
  if (state.kind === 'loading') {
    return (
      <div className="flex items-center gap-xs px-md py-xs text-xs text-text-subtle">
        <span className="inline-block w-2 h-2 rounded-full bg-text-subtle/40 animate-pulse" />
      </div>
    )
  }

  const cfg = MOOD_CONFIG[state.data.mood]
  const transitionClass = state.transitioning
    ? 'transition-all duration-600 opacity-0 scale-95'
    : 'transition-all duration-600 opacity-100 scale-100'

  return (
    <div className={`flex items-center justify-between px-md py-xs ${transitionClass}`}>
      <div className="flex items-center gap-xs text-xs text-text-subtle">
        <span
          className={`${cfg.color} ${cfg.animation} select-none`}
          title={`Lumen 正在·${cfg.label}中`}
          style={{ fontSize: '14px' }}
        >
          {cfg.icon}
        </span>
        <span>Lumen 正在·{cfg.label}中</span>
      </div>
      <a
        href="/inner-world"
        className="text-xs text-text-subtle hover:text-text-muted transition-colors"
      >
        查看 Lumen 的内心 →
      </a>
    </div>
  )
}
```

**动画 CSS（需追加到全局样式文件，通常是 `src/index.css` 或 `src/styles/globals.css`）：**

```css
/* Lumen MoodStrip 动画 */
@media (prefers-reduced-motion: no-preference) {
  @keyframes breathe {
    0%, 100% { transform: scale(0.95); opacity: 0.8; }
    50% { transform: scale(1.0); opacity: 1.0; }
  }
  @keyframes pulse-gentle {
    0%, 100% { opacity: 0.7; }
    50% { opacity: 1.0; }
  }
  @keyframes heartbeat {
    0%, 100% { transform: scale(0.98); }
    50% { transform: scale(1.02); }
  }
  @keyframes orbit {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }
  @keyframes bounce-soft {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-2px); }
  }

  .animate-breathe { animation: breathe 4s ease-in-out infinite; }
  .animate-pulse-gentle { animation: pulse-gentle 2s ease-in-out infinite; }
  .animate-heartbeat { animation: heartbeat 3s ease infinite; }
  .animate-orbit { animation: orbit 8s linear infinite; }
  .animate-bounce-soft { animation: bounce-soft 1s cubic-bezier(0.34, 1.56, 0.64, 1) infinite; }
}

@media (prefers-reduced-motion: reduce) {
  .animate-breathe,
  .animate-pulse-gentle,
  .animate-heartbeat,
  .animate-orbit,
  .animate-bounce-soft { animation: none; }
}
```

---

## 10. 修改 `src/pages/Chat.tsx`

在 `<ObservationStrip />` 下方紧接插入 `<MoodStrip />`：

```tsx
// 在文件顶部 import 区域加入
import MoodStrip from '../components/MoodStrip'

// 在 JSX 中，ObservationStrip 下方：
<ObservationStrip />
<MoodStrip />          {/* ← 新增 */}
<div className="scroll-auto-hide flex min-h-0 flex-1 ...">
  {/* ... 消息列表 ... */}
```

---

## 11. 内心世界页面占位（可选，Week 1 最后做）

在 `src/pages/` 新建 `InnerWorld.tsx`，Week 2 完善，Week 1 只需能路由到：

```tsx
export default function InnerWorld() {
  return (
    <div className="mx-auto max-w-[680px] px-md pt-xl">
      <h1 className="text-lg font-han text-ink mb-md">Lumen 的内心</h1>
      <p className="text-sm text-text-muted">正在建设中，Week 2 完善。</p>
    </div>
  )
}
```

在路由配置（通常是 `src/App.tsx` 或 `src/main.tsx`）中注册：
```tsx
import InnerWorld from './pages/InnerWorld'
// ...
<Route path="/inner-world" element={<InnerWorld />} />
```

---

## 验证步骤

1. `python -m uvicorn main:app --reload` 启动后端，确认无报错
2. `npm run dev` 启动前端
3. 打开 `http://localhost:5173`，Chat 页面 ObservationStrip 下方应出现情绪条
4. 加载时：短暂脉冲点
5. 加载后：显示"🌿 Lumen 正在·平静中"（初始状态 calm）
6. 发几条消息，等情绪推断运行，刷新页面看情绪是否有变化
7. `GET /api/companion/mood` 直接返回 JSON 确认接口

---

## 注意事项

- `conv` 对象在 `save_user_message()` 中可能没有 `user_id` 属性（取决于 `ensure_conversation` 返回的模型）。用 `getattr(conv, "user_id", "demo_user")` 安全获取。
- `presence.py:record_user_message()` 里不要 `await db.commit()`，由 `save_user_message()` 统一 commit，保证原子性。
- 情绪推断任务是 fire-and-forget，失败不影响对话。
- 动画 CSS 类名需在 Tailwind 配置中 safelist，或直接写内联 `style`。如果 Tailwind 用 `content:` 扫描模式，确保 `MoodStrip.tsx` 在扫描范围内（默认已包含 `src/**`）。
