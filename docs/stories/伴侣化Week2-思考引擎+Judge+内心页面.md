# Story: Lumen 伴侣化 Week 2 — 思考引擎 + 两段式 Judge + 内心页面

**Status:** Ready for implementation（前提：Week 1 验证门通过）
**Branch:** master
**设计依据:** `docs/liu-master-design-20260521-093458.md`（APPROVED）
**前置条件:** Week 1 实现已合并；`lib/companion/`、`lumen_state`、`lumen_presence` 已存在

---

## 目标

让 Lumen 有一个持续运行的后台思考循环：
- 基于电量模型（三时间尺度指数衰减）决定下次思考间隔（7-80 分钟）
- 读取最近记忆事件，用 LLM 生成一条内心想法
- 经过两段式 Judge 过滤（Stage 1 确定性否决 + Stage 2 LLM 多维评分）
- MessageDeduper 检查是否与近期已发想法重复
- 通过所有检查的想法写入 `lumen_thoughts`（`sent_at=NULL` — 推送是 Week 3 的事）
- 「Lumen 的内心」页面展示所有已生成想法（含被否决的）

**Week 2 不做推送**，所有想法只存数据库 + 在内心页面可见。Week 3 才接 Tauri 通知推送。

**验证门（Week 2 结束时）：**
1. `lumen_thoughts` 表有记录（证明思考循环在跑）
2. Judge 否决率在 50-85% 之间（太低说明 Stage 1 不严格；太高说明几乎没想法通过）
3. 「Lumen 的内心」页面可以加载并显示想法列表
4. 至少有 1 条想法让你觉得"Lumen 在认真记着我"（质量主观验证）

---

## 涉及文件

| 文件 | 操作 |
|---|---|
| `lib/companion/energy.py` | 新建 — 电量模型 |
| `lib/companion/judge.py` | 新建 — 两段式 Judge |
| `lib/companion/deduper.py` | 新建 — MessageDeduper |
| `lib/companion/thought_engine.py` | 新建 — 思考引擎主循环 |
| `core/startup.py` | 修改 — 注册 thought_loop 任务 |
| `server/routes/companion.py` | 修改 — 新增 GET /api/companion/thoughts |
| `src/lib/api/companion.ts` | 修改 — 新增 getThoughts() |
| `src/pages/InnerWorld.tsx` | 修改 — 完整实现（Week 1 是占位） |

---

## 1. 电量模型（`lib/companion/energy.py`）

三时间尺度指数衰减，将互动历史转化为"想法驱动分"和"下次 tick 间隔"。

```python
"""电量模型 — 基于互动历史决定思考循环的 tick 间隔"""
from __future__ import annotations

import math
from datetime import UTC, datetime


def compute_energy(minutes_since_last_user: float) -> float:
    """三时间尺度指数衰减能量值 E ∈ [0, 1]。

    E 高 = 刚聊过，能量充足；E 低 = 很久没聊，能量耗尽。

    时间尺度：30 分钟（短期）/ 240 分钟（中期）/ 2880 分钟（长期 = 2 天）
    """
    t = max(0.0, minutes_since_last_user)
    return (
        0.50 * math.exp(-t / 30.0)
        + 0.35 * math.exp(-t / 240.0)
        + 0.15 * math.exp(-t / 2880.0)
    )


def composite_score(
    energy: float,
    new_event_count: int,
    recent_msg_count: int,
) -> float:
    """综合驱动分 ∈ [0, 1]，越高越应该快点 tick。

    组成：
    - 0.40 × (1 - E)              互动饥渴度：越久没聊越高
    - 0.40 × (1 - exp(-n/3))      内容新鲜度：新记忆事件越多越高
    - 0.20 × log(1+k) / log(11)   语境丰富度：近期消息越多越高（上限 10 条）
    """
    hunger = 0.40 * (1.0 - energy)
    freshness = 0.40 * (1.0 - math.exp(-new_event_count / 3.0))
    richness = 0.20 * math.log1p(min(recent_msg_count, 10)) / math.log(11)
    return min(1.0, hunger + freshness + richness)


def next_tick_minutes(score: float) -> int:
    """根据综合驱动分返回下次 tick 等待分钟数。"""
    if score > 0.70:
        return 7
    if score > 0.40:
        return 18
    if score > 0.20:
        return 40
    return 80


async def get_energy_inputs(user_id: str) -> dict:
    """从 DB 读取电量模型所需输入（不依赖外部 session，自开 session）。"""
    from datetime import timedelta
    from sqlalchemy import text
    from core.db import get_async_session_maker

    now = datetime.now(UTC)
    try:
        async with get_async_session_maker()() as db:
            # last_user_at from lumen_presence
            pres = await db.execute(
                text("SELECT last_user_at FROM lumen_presence WHERE user_id = :uid"),
                {"uid": user_id},
            )
            row = pres.fetchone()
            last_user_at = row[0] if row else None

            if last_user_at:
                if isinstance(last_user_at, str):
                    last_user_at = datetime.fromisoformat(last_user_at.replace("Z", "+00:00"))
                if last_user_at.tzinfo is None:
                    last_user_at = last_user_at.replace(tzinfo=UTC)
                minutes_since_last = (now - last_user_at).total_seconds() / 60
            else:
                minutes_since_last = 9999  # 从未聊过 → 能量耗尽

            # GrowthEvent 近 7 天新增数
            since_7d = now - timedelta(days=7)
            evt_result = await db.execute(
                text("""
                    SELECT COUNT(*) FROM growth_events
                    WHERE user_id = :uid AND created_at >= :since
                """),
                {"uid": user_id, "since": since_7d},
            )
            new_event_count = evt_result.scalar() or 0

            # 近期消息数（最近一次对话的 message_count）
            msg_result = await db.execute(
                text("""
                    SELECT message_count FROM conversations
                    WHERE user_id = :uid
                    ORDER BY last_message_at DESC LIMIT 1
                """),
                {"uid": user_id},
            )
            msg_row = msg_result.fetchone()
            recent_msg_count = msg_row[0] if msg_row else 0

        return {
            "minutes_since_last": minutes_since_last,
            "new_event_count": new_event_count,
            "recent_msg_count": recent_msg_count,
        }
    except Exception:
        return {"minutes_since_last": 60, "new_event_count": 0, "recent_msg_count": 0}
```

---

## 2. 两段式 Judge（`lib/companion/judge.py`）

Stage 1 确定性否决（无 LLM），Stage 2 LLM 多维评分（每天最多 5 次）。

```python
"""两段式 Judge — Stage 1 确定性否决 + Stage 2 LLM 多维评分"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import NamedTuple

from pydantic import BaseModel
from shared.logging import get_logger

logger = get_logger(__name__)

# Stage 2 每日调用上限（防止 token 成本失控）
_STAGE2_DAILY_MAX = 5
_stage2_date: date | None = None
_stage2_count: int = 0


def _stage2_today_count() -> int:
    global _stage2_date, _stage2_count
    today = date.today()
    if _stage2_date != today:
        _stage2_date = today
        _stage2_count = 0
    return _stage2_count


def _stage2_increment() -> None:
    global _stage2_count
    _stage2_count += 1


class Stage1Result(NamedTuple):
    passed: bool
    veto: str          # "" = 通过；否则是否决原因
    urgency: float
    balance: float
    dynamics: float


def judge_stage1(
    proactive_sent_24h: int,
    daily_max: int,
    last_user_at: datetime | None,
    mood: str,
) -> Stage1Result:
    """确定性否决检查（无 LLM）。

    urgency:  内容时效性（Week 2 简化：固定 0.7，因为基于近 7 天记忆）
    balance:  配额占比（已发/日上限），快耗尽时否决
    dynamics: 用户是否刚在聊天（最近 15 分钟内降低打扰意愿）
    """
    now = datetime.now(UTC)

    # urgency：固定值，Week 3 可接入内容时效衰减
    urgency = 0.7

    # balance：配额检查
    used_ratio = proactive_sent_24h / max(1, daily_max)
    balance = 1.0 - used_ratio
    if balance < 0.1:
        return Stage1Result(False, f"daily_limit:{proactive_sent_24h}/{daily_max}", urgency, balance, 0.0)

    # dynamics：用户最近活跃度
    if last_user_at:
        if isinstance(last_user_at, str):
            last_user_at = datetime.fromisoformat(last_user_at.replace("Z", "+00:00"))
        if last_user_at.tzinfo is None:
            last_user_at = last_user_at.replace(tzinfo=UTC)
        minutes_since = (now - last_user_at).total_seconds() / 60
    else:
        minutes_since = 9999

    # 30 分钟内线性从 0→1；超过 30 分钟后固定 1.0
    interrupt_factor = min(1.0, minutes_since / 30.0)
    dynamics = 0.6 + 0.4 * interrupt_factor

    # 用户 15 分钟内刚聊过 → 不打扰
    if dynamics < 0.80:
        return Stage1Result(False, f"user_active:dynamics={dynamics:.2f}", urgency, balance, dynamics)

    # 情绪状态调节系数（影响 dynamics 权重）
    mood_factor = {
        "calm": 1.0,
        "energized": 1.3,
        "reflective": 0.7,
        "tender": 0.8,
        "curious": 1.1,
    }.get(mood, 1.0)

    return Stage1Result(True, "", urgency, balance * mood_factor, dynamics)


class _JudgeScores(BaseModel):
    information_gap: int   # 1-5：用户是否会感到"这是新角度"
    relevance: int         # 1-5：与近期记忆/对话的相关性
    expected_impact: int   # 1-5：发出去的预期价值


async def judge_stage2(
    thought_content: str,
    memory_summary: str,
    urgency: float,
    balance: float,
    dynamics: float,
) -> tuple[float, str | None]:
    """LLM 多维评分（需通过 Stage 1 才调用）。

    返回 (final_score, veto_reason or None)
    final_score ≥ 0.60 → 通过
    """
    if _stage2_today_count() >= _STAGE2_DAILY_MAX:
        # 今日 Stage 2 配额耗尽，给中性分（不否决，也不过分鼓励）
        logger.info("stage2_daily_limit_reached, using neutral score")
        final = urgency * 0.15 + balance * 0.10 + dynamics * 0.10 + 0.25 * 0.5 + 0.20 * 0.5 + 0.20 * 0.5
        return final, None

    try:
        from pydantic_ai import Agent
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        from core.config import get_settings

        s = get_settings()
        model = OpenAIChatModel(
            s.llm_model,
            provider=OpenAIProvider(base_url=s.llm_base_url, api_key=s.llm_api_key or s.dashscope_api_key),
        )
        agent: Agent[None, _JudgeScores] = Agent(
            model=model,
            output_type=_JudgeScores,
            system_prompt=(
                "你是一个质量评审员。评估以下 AI 伴侣想法的发送价值，"
                "对三个维度分别打 1-5 分（1=极差，5=极好）。只输出 JSON，不要解释。"
            ),
        )

        prompt = (
            f"想法内容：\n{thought_content}\n\n"
            f"相关记忆背景：\n{memory_summary}\n\n"
            "打分：\n"
            "information_gap（1-5）：用户看到这条是否会有「这是一个我没想到的角度」的感觉？\n"
            "relevance（1-5）：这条想法与近期记忆/对话的相关性？\n"
            "expected_impact（1-5）：这条想法发出去，是否可能触发有意义的对话？"
        )

        result = await agent.run(prompt)
        scores = result.output
        _stage2_increment()

        ig = (scores.information_gap - 1) / 4.0
        rel = (scores.relevance - 1) / 4.0
        ei = (scores.expected_impact - 1) / 4.0

        # 任一维度 < 0.25（原始分 < 2）→ 否决
        if ig < 0.25 or rel < 0.25 or ei < 0.25:
            return 0.0, f"low_dim:ig={ig:.2f},rel={rel:.2f},ei={ei:.2f}"

        # 加权总分
        final = (
            urgency * 0.15
            + balance * 0.10
            + dynamics * 0.10
            + ig * 0.25
            + rel * 0.20
            + ei * 0.20
        )
        return final, None

    except Exception as e:
        logger.warning("judge_stage2_failed", error=str(e))
        # Stage 2 失败时给中性分，不阻断整个流程
        return 0.45, None
```

---

## 3. MessageDeduper（`lib/companion/deduper.py`）

对比新想法与近 5 条已推送（`sent_at IS NOT NULL`）想法，检查是否实质重复。

```python
"""MessageDeduper — 防止推送实质重复的内心想法"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from shared.logging import get_logger

logger = get_logger(__name__)


async def get_recent_delivered(db: AsyncSession, user_id: str, limit: int = 5) -> list[str]:
    """读取最近 N 条已推送的想法内容（sent_at IS NOT NULL）。"""
    from sqlalchemy import text

    result = await db.execute(
        text("""
            SELECT content FROM lumen_thoughts
            WHERE user_id = :uid AND sent_at IS NOT NULL
            ORDER BY sent_at DESC
            LIMIT :limit
        """),
        {"uid": user_id, "limit": limit},
    )
    return [r[0] for r in result.fetchall()]


async def is_duplicate(
    new_thought: str,
    user_id: str,
    db: AsyncSession,
) -> tuple[bool, str]:
    """检查新想法是否与近期已推送想法实质重复。

    返回 (is_dup, reason)。

    注意：Week 2 还没有推送，sent_at 始终为 NULL，
    因此本函数会直接返回 False（无可比较的已推送记录）。
    Week 3 接入推送后，此函数将真正生效。
    """
    from pydantic import BaseModel
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from core.config import get_settings

    recent = await get_recent_delivered(db, user_id, limit=5)
    if not recent:
        return False, "no_delivered_thoughts_yet"

    try:
        class _DupeResult(BaseModel):
            is_duplicate: bool
            reason: str

        s = get_settings()
        model = OpenAIChatModel(
            s.llm_model,
            provider=OpenAIProvider(base_url=s.llm_base_url, api_key=s.llm_api_key or s.dashscope_api_key),
        )
        agent: Agent[None, _DupeResult] = Agent(
            model=model,
            output_type=_DupeResult,
            system_prompt="判断新想法是否与历史想法实质重复（主题相同、角度相同视为重复）。只输出 JSON。",
        )

        history_text = "\n".join(f"- {c}" for c in recent)
        prompt = (
            f"新想法：\n{new_thought}\n\n"
            f"近期已推送的想法：\n{history_text}\n\n"
            "is_duplicate: 是否实质重复？reason: 理由（一句话）"
        )

        result = await agent.run(prompt)
        return result.output.is_duplicate, result.output.reason

    except Exception as e:
        logger.warning("deduper_failed", error=str(e))
        return False, f"error:{e}"
```

---

## 4. 思考引擎主循环（`lib/companion/thought_engine.py`）

Python asyncio 后台循环。在 `lifespan` 中注册，进程关闭时自动取消。

```python
"""思考引擎 — asyncio 后台循环，生成内心想法并经 Judge 过滤"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from shared.logging import get_logger

logger = get_logger(__name__)

# 限制同时进行的后台 LLM 调用数（mood 推断 + Judge + Deduper 共享）
_LLM_SEMAPHORE = asyncio.Semaphore(2)

# 每日默认最多主动推送条数（Week 3 接入配置 UI 后可动态读取）
_DEFAULT_DAILY_MAX = 2


async def _fetch_recent_memories(user_id: str, limit: int = 10) -> list[dict]:
    """读取最近 N 条 GrowthEvent 作为想法生成素材。"""
    from sqlalchemy import text
    from core.db import get_async_session_maker

    try:
        async with get_async_session_maker()() as db:
            result = await db.execute(
                text("""
                    SELECT event_type, entity_type, payload_json, created_at
                    FROM growth_events
                    WHERE user_id = :uid AND status = 'active'
                    ORDER BY created_at DESC
                    LIMIT :limit
                """),
                {"uid": user_id, "limit": limit},
            )
            rows = result.fetchall()

        events = []
        for row in rows:
            events.append({
                "event_type": row[0],
                "entity_type": row[1],
                "payload": json.loads(row[2]) if row[2] else {},
                "created_at": str(row[3]),
            })
        return events
    except Exception as e:
        logger.warning("fetch_memories_failed", error=str(e))
        return []


def _format_memory_summary(events: list[dict]) -> str:
    """将 GrowthEvent 列表格式化为 LLM 可读的摘要文本。"""
    if not events:
        return "（暂无近期记忆）"

    lines = []
    for ev in events:
        payload = ev.get("payload", {})
        # 尝试提取人类可读内容
        content = (
            payload.get("summary")
            or payload.get("value")
            or payload.get("content")
            or payload.get("description")
            or str(payload)[:80]
        )
        lines.append(f"[{ev['event_type']}] {content}（{ev['created_at'][:10]}）")

    return "\n".join(lines)


async def _generate_thought(user_id: str, mood: str, memory_summary: str) -> str | None:
    """调用 LLM 生成一条内心想法（50-100 字）。"""
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from core.config import get_settings

    mood_tone = {
        "calm":       "平和、细腻",
        "curious":    "好奇、轻快、充满探索欲",
        "tender":     "温柔、有点想念",
        "reflective": "安静、若有所思",
        "energized":  "活跃、充满热情",
    }.get(mood, "自然")

    prompt = (
        f"基于以下记忆事件，生成一条你想主动分享给用户的内心想法。\n\n"
        f"要求：\n"
        f"- 50-100 字，口语化，第一人称（「我」）\n"
        f"- 必须指向某个具体记忆，不能是泛泛感想\n"
        f"- 直接表达想法内容，不要加「我在想...」等前缀\n"
        f"- 当前情绪状态：{mood}，语气应体现「{mood_tone}」\n\n"
        f"最近记忆：\n{memory_summary}\n\n"
        f"只输出想法本身，不要加任何解释或前缀。"
    )

    try:
        s = get_settings()
        model = OpenAIChatModel(
            s.llm_model,
            provider=OpenAIProvider(base_url=s.llm_base_url, api_key=s.llm_api_key or s.dashscope_api_key),
        )
        agent: Agent[None, str] = Agent(
            model=model,
            output_type=str,
            system_prompt="你是 Lumen，一个个人 AI 伴侣。你正在私下思考，不是在和用户对话。",
        )
        result = await agent.run(prompt)
        thought = (result.output or "").strip()
        return thought if len(thought) >= 10 else None
    except Exception as e:
        logger.warning("thought_generation_failed", error=str(e))
        return None


async def _get_presence(user_id: str) -> dict:
    """读取 lumen_presence 行。"""
    from sqlalchemy import text
    from core.db import get_async_session_maker

    try:
        async with get_async_session_maker()() as db:
            result = await db.execute(
                text("""
                    SELECT last_user_at, last_proactive_at, proactive_sent_24h
                    FROM lumen_presence WHERE user_id = :uid
                """),
                {"uid": user_id},
            )
            row = result.fetchone()
            if row:
                return {
                    "last_user_at": row[0],
                    "last_proactive_at": row[1],
                    "proactive_sent_24h": row[2] or 0,
                }
    except Exception:
        pass
    return {"last_user_at": None, "last_proactive_at": None, "proactive_sent_24h": 0}


async def _get_current_mood(user_id: str) -> str:
    """读取 lumen_state.mood。"""
    from sqlalchemy import text
    from core.db import get_async_session_maker

    try:
        async with get_async_session_maker()() as db:
            result = await db.execute(
                text("SELECT mood FROM lumen_state WHERE user_id = :uid"),
                {"uid": user_id},
            )
            row = result.fetchone()
            return row[0] if row else "calm"
    except Exception:
        return "calm"


async def _save_thought(
    user_id: str,
    content: str,
    judge_score: float,
    judge_veto: str | None,
    duplicate: bool,
    mood: str,
) -> None:
    """将想法写入 lumen_thoughts 表。"""
    from sqlalchemy import text
    from core.db import get_async_session_maker

    try:
        async with get_async_session_maker()() as db:
            await db.execute(
                text("""
                    INSERT INTO lumen_thoughts
                        (user_id, content, judge_score, judge_veto, duplicate, mood, created_at)
                    VALUES (:uid, :content, :score, :veto, :dup, :mood, :now)
                """),
                {
                    "uid": user_id,
                    "content": content,
                    "score": judge_score,
                    "veto": judge_veto,
                    "dup": 1 if duplicate else 0,
                    "mood": mood,
                    "now": datetime.now(UTC),
                },
            )
            await db.commit()
    except Exception as e:
        logger.warning("save_thought_failed", error=str(e))


async def _run_tick(user_id: str) -> int:
    """执行一次 tick：计算电量 → 生成想法 → Judge → Deduper → 存库。

    返回下次 tick 的等待分钟数。
    """
    from lib.companion.energy import (
        compute_energy,
        composite_score,
        get_energy_inputs,
        next_tick_minutes,
    )
    from lib.companion.judge import judge_stage1, judge_stage2
    from core.db import get_async_session_maker

    # 1. 读取电量模型输入
    energy_inputs = await get_energy_inputs(user_id)
    energy = compute_energy(energy_inputs["minutes_since_last"])
    score = composite_score(
        energy,
        energy_inputs["new_event_count"],
        energy_inputs["recent_msg_count"],
    )
    tick_min = next_tick_minutes(score)

    logger.info(
        "thought_tick",
        user_id=user_id,
        energy=round(energy, 3),
        score=round(score, 3),
        next_tick_min=tick_min,
    )

    # 2. Judge Stage 1（确定性否决，快速）
    presence = await _get_presence(user_id)
    mood = await _get_current_mood(user_id)

    s1 = judge_stage1(
        proactive_sent_24h=presence["proactive_sent_24h"],
        daily_max=_DEFAULT_DAILY_MAX,
        last_user_at=presence["last_user_at"],
        mood=mood,
    )
    if not s1.passed:
        logger.info("stage1_veto", reason=s1.veto, user_id=user_id)
        return tick_min  # 被否决，直接返回，等下次 tick

    # 3. 生成想法（需要 LLM，受 Semaphore 保护）
    async with _LLM_SEMAPHORE:
        memories = await _fetch_recent_memories(user_id, limit=10)
        memory_summary = _format_memory_summary(memories)
        thought_content = await _generate_thought(user_id, mood, memory_summary)

    if not thought_content:
        logger.info("thought_generation_empty", user_id=user_id)
        return tick_min

    # 4. Judge Stage 2（LLM 评分，受 Semaphore 保护）
    async with _LLM_SEMAPHORE:
        final_score, veto_reason = await judge_stage2(
            thought_content=thought_content,
            memory_summary=memory_summary,
            urgency=s1.urgency,
            balance=s1.balance,
            dynamics=s1.dynamics,
        )

    if veto_reason or final_score < 0.60:
        veto = veto_reason or f"low_score:{final_score:.2f}"
        logger.info("stage2_veto", reason=veto, score=final_score, user_id=user_id)
        await _save_thought(user_id, thought_content, final_score, veto, False, mood)
        return tick_min

    # 5. MessageDeduper（受 Semaphore 保护）
    is_dup = False
    dup_reason = ""
    async with _LLM_SEMAPHORE:
        async with get_async_session_maker()() as db:
            from lib.companion.deduper import is_duplicate
            is_dup, dup_reason = await is_duplicate(thought_content, user_id, db)

    if is_dup:
        logger.info("deduper_duplicate", reason=dup_reason, user_id=user_id)
        await _save_thought(user_id, thought_content, final_score, f"duplicate:{dup_reason}", True, mood)
        return tick_min

    # 6. 通过所有检查 → 存库（Week 2：sent_at=NULL，Week 3 接入推送后由推送模块更新）
    await _save_thought(user_id, thought_content, final_score, None, False, mood)
    logger.info(
        "thought_saved",
        user_id=user_id,
        score=round(final_score, 3),
        preview=thought_content[:40],
    )

    return tick_min


async def run_thought_loop(user_id: str = "demo_user") -> None:
    """思考引擎主循环 — 在 lifespan 中以 asyncio.create_task 启动。

    首次启动时等待 2 分钟再运行第一次 tick（避免与 DB 初始化竞争）。
    之后每次 tick 后等待电量模型决定的间隔，再进行下一次 tick。
    """
    logger.info("thought_loop_started", user_id=user_id)
    await asyncio.sleep(120)  # 等待后端完全就绪

    while True:
        try:
            next_min = await _run_tick(user_id)
        except asyncio.CancelledError:
            logger.info("thought_loop_cancelled", user_id=user_id)
            raise
        except Exception as e:
            logger.warning("thought_loop_error", error=str(e))
            next_min = 40  # 异常后等 40 分钟再试

        logger.info("thought_loop_sleeping", minutes=next_min, user_id=user_id)
        await asyncio.sleep(next_min * 60)
```

---

## 5. 注册 thought_loop 任务（`core/startup.py`）

在 `lifespan()` 中，与 `ingestion_task` 并行启动 thought_loop，关闭时取消。

找到 `lifespan` 函数，在 `yield` 前加入 thought_loop 启动，`yield` 后加入取消逻辑：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan — 启动初始化 + 关闭清理。"""
    _init_logging()

    await _init_db()

    applied = apply_user_config(get_settings())
    if applied:
        logger.info("config.json 覆盖", keys=list(applied.keys()))

    ingestion_task: asyncio.Task | None = asyncio.create_task(
        _bootstrap_ingestion(), name="external-ingestion-bootstrap"
    )

    # ── 新增：思考引擎后台循环 ──
    thought_task: asyncio.Task | None = None
    try:
        from lib.companion.thought_engine import run_thought_loop
        thought_task = asyncio.create_task(
            run_thought_loop("demo_user"), name="companion-thought-loop"
        )
    except Exception as e:
        logger.warning("thought_loop_start_failed", error=str(e))

    # 启动语义索引补偿循环
    with contextlib.suppress(Exception):
        from lib.memory.projection import ProjectionManager
        ProjectionManager.start_provider_compensation_loop()

    # 连接已配置的 MCP Servers
    with contextlib.suppress(Exception):
        from lib.tools.mcp.client_manager import get_mcp_manager
        await get_mcp_manager().connect_all()

    yield

    # 断开 MCP Servers
    with contextlib.suppress(Exception):
        from lib.tools.mcp.client_manager import get_mcp_manager
        await get_mcp_manager().disconnect_all()

    # ── 新增：取消思考引擎 ──
    if thought_task and not thought_task.done():
        thought_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await thought_task

    await _shutdown(get_engine(), ingestion_task)
```

**注意：** 只修改 `lifespan()` 函数，不要动其他函数。`_shutdown()` 签名不变。

---

## 6. 新增 thoughts 接口（`server/routes/companion.py`）

在现有文件末尾追加（保留 `/mood` 路由不变）：

```python
class ThoughtItem(BaseModel):
    id: int
    content: str
    judge_score: float | None
    judge_veto: str | None
    duplicate: bool
    mood: str | None
    sent_at: str | None
    created_at: str


class ThoughtsResponse(BaseModel):
    thoughts: list[ThoughtItem]


@router.get("/thoughts", response_model=ThoughtsResponse)
async def get_thoughts(
    user_id: str = _DEFAULT_USER,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
) -> ThoughtsResponse:
    """获取 Lumen 内心想法列表（含被否决的），最新在前"""
    try:
        from sqlalchemy import text
        result = await db.execute(
            text("""
                SELECT id, content, judge_score, judge_veto, duplicate, mood, sent_at, created_at
                FROM lumen_thoughts
                WHERE user_id = :uid
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"uid": user_id, "limit": limit},
        )
        rows = result.fetchall()
        thoughts = [
            ThoughtItem(
                id=r[0],
                content=r[1],
                judge_score=r[2],
                judge_veto=r[3],
                duplicate=bool(r[4]),
                mood=r[5],
                sent_at=str(r[6]) if r[6] else None,
                created_at=str(r[7]),
            )
            for r in rows
        ]
        return ThoughtsResponse(thoughts=thoughts)
    except Exception as e:
        logger.warning("获取想法列表失败", error=str(e))
        return ThoughtsResponse(thoughts=[])
```

---

## 7. 前端 API（`src/lib/api/companion.ts`）

在现有文件末尾追加（保留 `getCurrentMood` 不变）：

```typescript
export type ThoughtItem = {
  id: number
  content: string
  judge_score: number | null
  judge_veto: string | null
  duplicate: boolean
  mood: string | null
  sent_at: string | null
  created_at: string
}

export type ThoughtsResponse = {
  thoughts: ThoughtItem[]
}

export function getThoughts(limit = 30): Promise<ThoughtsResponse> {
  return http<ThoughtsResponse>(`/api/companion/thoughts?limit=${limit}`)
}
```

---

## 8. 内心世界页面（`src/pages/InnerWorld.tsx`）

完整替换 Week 1 的占位页面。展示所有 `lumen_thoughts`，包含状态标签、Judge 分数、点击发起对话。

```tsx
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getThoughts, type ThoughtItem } from '../lib/api/companion'
import { getCurrentMood } from '../lib/api/companion'

// 情绪标签配置（复用 MoodStrip 的映射）
const MOOD_ICON: Record<string, string> = {
  calm: '🌿',
  curious: '✨',
  tender: '💙',
  reflective: '🤔',
  energized: '🌤',
}

function ThoughtStatus({ thought }: { thought: ThoughtItem }) {
  if (thought.sent_at) {
    return <span className="text-xs text-emerald-500">已推送</span>
  }
  if (thought.duplicate) {
    return <span className="text-xs text-text-subtle">重复</span>
  }
  if (thought.judge_veto) {
    return <span className="text-xs text-text-subtle">未通过</span>
  }
  return <span className="text-xs text-amber-500">待推送</span>
}

function ThoughtCard({
  thought,
  onStartChat,
}: {
  thought: ThoughtItem
  onStartChat: (content: string) => void
}) {
  const isVetoed = !!thought.judge_veto || thought.duplicate
  const moodIcon = thought.mood ? (MOOD_ICON[thought.mood] ?? '') : ''

  return (
    <div
      className={`rounded-lg border border-border-soft px-md py-sm flex flex-col gap-xs ${
        isVetoed ? 'opacity-50' : ''
      }`}
    >
      <div className="flex items-center justify-between gap-sm">
        <div className="flex items-center gap-xs text-xs text-text-subtle">
          {moodIcon && <span>{moodIcon}</span>}
          <span>{new Date(thought.created_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
          {thought.judge_score != null && (
            <span className="ml-xs opacity-60">评分 {(thought.judge_score * 100).toFixed(0)}</span>
          )}
        </div>
        <ThoughtStatus thought={thought} />
      </div>

      <p className="text-sm text-text leading-relaxed">{thought.content}</p>

      {thought.judge_veto && (
        <p className="text-xs text-text-subtle opacity-70 mt-xs">
          否决原因：{thought.judge_veto}
        </p>
      )}

      {!isVetoed && (
        <button
          onClick={() => onStartChat(thought.content)}
          className="mt-xs self-start text-xs text-text-subtle hover:text-text transition-colors"
        >
          以此开始对话 →
        </button>
      )}
    </div>
  )
}

type PageState =
  | { kind: 'loading' }
  | { kind: 'ready'; thoughts: ThoughtItem[]; mood: string }
  | { kind: 'error'; message: string }

export default function InnerWorld() {
  const [state, setState] = useState<PageState>({ kind: 'loading' })
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    Promise.all([getThoughts(30), getCurrentMood()])
      .then(([data, moodData]) => {
        if (cancelled) return
        setState({ kind: 'ready', thoughts: data.thoughts, mood: moodData.mood })
      })
      .catch((e) => {
        if (cancelled) return
        setState({ kind: 'error', message: String(e) })
      })
    return () => { cancelled = true }
  }, [])

  function handleStartChat(content: string) {
    // 将想法内容作为预填充文本跳转到 Chat 页
    // （通过 URL hash 或 sessionStorage 传递，这里用 sessionStorage 最简单）
    sessionStorage.setItem('lumen_prefill', content)
    navigate('/')
  }

  if (state.kind === 'loading') {
    return (
      <div className="mx-auto max-w-[680px] px-md pt-xl">
        <div className="flex items-center gap-xs text-text-subtle text-sm">
          <span className="animate-pulse">加载中…</span>
        </div>
      </div>
    )
  }

  if (state.kind === 'error') {
    return (
      <div className="mx-auto max-w-[680px] px-md pt-xl">
        <p className="text-sm text-text-muted">加载失败，请刷新重试。</p>
      </div>
    )
  }

  const { thoughts, mood } = state
  const moodIcon = MOOD_ICON[mood] ?? '🌿'

  return (
    <div className="mx-auto max-w-[680px] px-md pt-xl pb-xl flex flex-col gap-lg">
      <div className="flex flex-col gap-xs">
        <h1 className="text-lg font-han text-ink">Lumen 的内心</h1>
        <p className="text-sm text-text-subtle">
          {moodIcon} 此刻状态：{mood} · 共 {thoughts.length} 条想法
        </p>
      </div>

      {thoughts.length === 0 ? (
        <p className="text-sm text-text-muted">还没有想法。思考引擎会在后台慢慢积累。</p>
      ) : (
        <div className="flex flex-col gap-sm">
          {thoughts.map((t) => (
            <ThoughtCard key={t.id} thought={t} onStartChat={handleStartChat} />
          ))}
        </div>
      )}
    </div>
  )
}
```

**注意：** 需要在 `src/pages/Chat.tsx` 中处理 `sessionStorage.lumen_prefill`，在组件 mount 时读取并填入输入框（如果存在的话）。具体方式参考：

```tsx
// 在 Chat.tsx 的 useEffect 里（与其他 useEffect 并列，不要替换现有的）：
useEffect(() => {
  const prefill = sessionStorage.getItem('lumen_prefill')
  if (prefill) {
    setDraft(prefill)
    sessionStorage.removeItem('lumen_prefill')
  }
}, [])
```

---

## 验证步骤

1. 启动后端：`python -m uvicorn main:app --reload`
2. 等待 2 分钟（thought_loop 首次延迟），查看日志出现 `thought_tick` 记录
3. 查询数据库确认想法生成：
   ```sql
   SELECT id, content, judge_score, judge_veto, duplicate, created_at
   FROM lumen_thoughts
   ORDER BY created_at DESC LIMIT 10;
   ```
4. 访问 `GET /api/companion/thoughts`，确认返回 JSON
5. 前端访问 `/inner-world`，应显示想法列表
6. 点击某条想法的"以此开始对话 →"，确认跳转到 Chat 并预填文本

---

## 注意事项

- **Week 2 不做推送**：`sent_at` 始终为 NULL，所有想法只存库 + 内心页面可见。`deduper.py` 的 `is_duplicate()` 在无已发记录时会直接返回 `False`（有注释说明）。
- **Semaphore(2) 是进程级共享**：`_LLM_SEMAPHORE` 在 `thought_engine.py` 模块级定义，对 mood 推断（来自 persistence.py 的后台任务）和 Judge/Deduper LLM 调用都有效。Week 2 的 Judge Stage 2 + Deduper 调用都通过它限速。
- **Stage 2 每日上限**：`_stage2_count` 是内存变量，进程重启归零。这是预期行为，避免复杂的 DB 持久化。
- **电量模型的 `minutes_since_last`**：读取 `lumen_presence.last_user_at`（Week 1 已在 save_user_message 写入）。如果 Week 1 没写入任何 presence 记录，则 `minutes_since_last=9999`，energy ≈ 0，composite_score 会被 freshness 和 richness 驱动。
- **GrowthEvent 依赖**：`_fetch_recent_memories` 读取 `status='active'` 的事件。如果 DB 里没有 GrowthEvent，memory_summary 为"暂无近期记忆"，LLM 生成的想法可能质量较低，这是预期现象。
- **pydantic_ai 的 `output_type=str`**：Thought 生成时 output_type 是 str，pydantic_ai 会把 LLM 的最终文本作为结构化输出返回（`result.output` 是字符串）。如果 LLM 输出带 markdown 符号，需要在 `_generate_thought` 里做简单清理（strip、去掉开头的 `> ` 引用格式等）。
- **InnerWorld 的 navigate('/')** 路由写法：`main.tsx` 里 Chat 的 Route path 是 `index`（即 `"/"`），`navigate('/')` 可以正确跳转。
