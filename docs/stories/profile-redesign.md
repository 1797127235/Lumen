# 画像层重新设计：从结构化简历到 AI 认知画像

> 实现文档 · 2026-05-11

## 1. 背景

当前 Lumen 的画像页面（Profile）本质上是一个 CRUD 表单：基础信息 12 个字段 + 技能列表 + 经历列表。页面标题叫"AI 眼中的你"，但内容完全是结构化简历，缺少"AI 对用户的综合理解"。

Agent 看到的上下文（`snapshot.py` L0 固定块）也是字段拼接：

```
## 身份
- 学校：XX大学
- 专业：计算机科学
- 年级：大三

## 目标
- 找实习：大厂后端岗位
```

没有自然语言的综合画像。对比 akashic-agent 的 SELF.md 机制（三段式："人格与形象" / "我对当前用户的理解" / "我们关系的定义"），Lumen 缺少的关键层是：**LLM 从全部对话和事件中综合生成的自然语言画像**。

## 2. 设计目标

页面核心体验从 **"查看和编辑表单"** 变为 **"阅读 AI 对你的理解"**。

四个区块：

| 区块 | 内容 | 数据来源 | 更新时机 |
|------|------|---------|---------|
| 关于你 | AI 自然语言综合画像（2-3 段） | LLM 从全部 growth_events 综合 | 每次对话后异步 |
| AI 注意到的 | 行为模式和偏好洞察卡片 | 从偏好/决策/状态事件提炼 | 积累 3+ 条同类事件后 |
| 此刻 | 当前状态动态快照 | 最近的 status_changed + goal_updated | 实时 |
| 你走过的路 | 成长时间线 | 全部 growth_events 按时间排序 | 实时 |

原有的基础信息/技能/经历编辑功能 **保留但折叠到底部**，降级为"简历数据"补充。

## 3. 核心机制：AI 综合画像生成器

借鉴 akashic-agent 的 `MemoryOptimizer`，新增 `backend/memory/understanding.py`。

### 3.1 触发时机

在 `facade.py` 的 `flush_projections()` 中，对话结束写入事件后，异步触发画像更新：

```python
# facade.py — flush_projections 追加
async def flush_projections(self, user_id: str, event_ids: list[str] | None = None) -> None:
    await sync_user_md_projection(user_id)
    invalidate_cache(user_id)
    if event_ids:
        task = asyncio.create_task(self._sync_cognee(event_ids, user_id=user_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        # 新增：异步更新 AI 综合画像
        task2 = asyncio.create_task(self._update_understanding(user_id))
        _background_tasks.add(task2)
        task2.add_done_callback(_background_tasks.discard)
```

### 3.2 画像生成 Prompt

核心 prompt 设计（借鉴 akashic 的"缺席成本测试"）：

```
你是一个 AI 伴侣的用户画像专家。基于以下事件数据，生成一段关于这个用户的综合画像。

## 规则
1. 用第二人称（"你"），像向用户本人介绍他们自己
2. 写 2-3 段自然语言，总长 300-500 字
3. 只包含有证据支撑的观察，不编造
4. 使用"缺席成本测试"：6 个月后全新对话中，缺少此信息是否会导致 AI 方向性失误？是→必须包含
5. 三类内容优先：
   - 用户事实（身份背景、经历轨迹）
   - 用户偏好（思维方式、价值取向、沟通风格）
   - 关键决策和转折点
6. 以下内容不要包含：
   - 可从上下文推导的信息
   - 临时状态（用"此刻"区块展示）
   - 过于细节的技术操作信息

## 现有画像
{existing_about_you}

## 全部事件数据
{events_summary}

## 输出
直接输出画像文本，不要加标题或分隔线。
```

### 3.3 模式洞察提取

从同类事件积累中提取模式：

```python
_PATTERN_CATEGORIES = {
    "time_preference": "时间偏好",
    "learning_style": "学习风格",
    "decision_pattern": "决策模式",
    "value_orientation": "价值取向",
    "communication_style": "沟通风格",
}

# 触发条件：某类事件 >= 3 条时，调用 LLM 提炼模式
# 示例输出：
# {"insight": "你习惯夜间编码，效率最高通常在 22:00 后",
#  "category": "time_preference",
#  "evidence_count": 5,
#  "first_seen": "2024-03-01",
#  "updated_at": "2024-05-11"}
```

## 4. 数据存储方案

**不新增数据库表**。利用现有设施：

### 4.1 新增 .md 投影文件

```
~/.lumen/memory/{user_id}/
├── memory.md        # 现有（保留不变）
├── about_you.md     # 新增：AI 自然语言综合画像
├── patterns.md      # 新增：模式洞察列表
├── skills.md        # 现有（保留不变）
├── experiences.md   # 现有（保留不变）
└── documents.md     # 现有（保留不变）
```

### 4.2 新增常量

```python
# backend/memory/constants.py 追加
MD_CHAR_LIMITS: dict[str, int] = {
    "memory": 4000,
    "skills": 3000,
    "experiences": 5000,
    "about_you": 2000,    # 新增
    "patterns": 2000,     # 新增
}
```

### 4.3 UserProfile.profile_data JSON 字段

```python
# UserProfile.profile_data 新增字段（无需改 ORM）：
{
  # 现有字段保持不变...

  # 新增
  "ai_understanding": str,       # "关于你"自然语言文本
  "ai_understanding_updated_at": str,  # ISO datetime
  "patterns": [                  # 模式洞察列表
    {
      "insight": str,            # 洞察描述
      "category": str,           # time_preference / learning_style / ...
      "evidence_count": int,     # 支撑事件数
      "first_seen": str,         # ISO date
      "updated_at": str          # ISO datetime
    }
  ]
}
```

> 注意：UserProfile ORM 的 `profile_data` 已是 `JSON` 类型，直接存即可，不需要改模型。

## 5. 后端逐文件改动

### 5.1 新建 `backend/memory/understanding.py`

核心文件。职责：
- `update_ai_understanding(user_id: str) -> str`：从全部 events 生成/更新"关于你"画像
- `detect_patterns(user_id: str) -> list[dict]`：提取模式洞察
- `get_about_you(user_id: str) -> AboutYouData`：读取画像数据

实现要点：

```python
"""AI 综合画像生成器。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select

from backend.db import get_async_session_maker
from backend.logging_config import get_logger
from backend.domain.models import GrowthEvent

logger = get_logger(__name__)


@dataclass
class AboutYouData:
    about_you: str = ""
    updated_at: str = ""
    patterns: list[dict] = field(default_factory=list)
    now_status: dict = field(default_factory=dict)  # 最新状态
    journey: list[dict] = field(default_factory=list)  # 时间线


async def _get_all_events_summary(user_id: str) -> str:
    """将全部 growth_events 汇总为文本，供 LLM 生成画像。"""
    async with get_async_session_maker()() as db:
        stmt = (
            select(GrowthEvent)
            .where(GrowthEvent.user_id == user_id)
            .order_by(GrowthEvent.created_at.desc())
        )
        result = await db.execute(stmt)
        events = list(result.scalars().all())

    if not events:
        return ""

    lines = []
    for event in events[:50]:  # 最多取 50 条，避免 token 过长
        payload = ""
        if event.payload_json:
            try:
                p = json.loads(event.payload_json)
                if isinstance(p, dict):
                    # 提取核心内容字段
                    payload = (
                        p.get("content")
                        or p.get("value")
                        or p.get("memory_md")
                        or p.get("description")
                        or json.dumps(p, ensure_ascii=False)[:100]
                    )
            except json.JSONDecodeError:
                pass
        lines.append(f"[{event.event_type}] {payload[:120]}")

    return "\n".join(lines)


async def update_ai_understanding(user_id: str) -> str:
    """生成/更新 AI 综合画像。返回画像文本。"""
    from backend.memory.markdown import read_about_you, write_about_you

    events_summary = await _get_all_events_summary(user_id)
    existing = read_about_you(user_id)

    if not events_summary:
        return existing

    # 调用 LLM 生成画像
    new_text = await _generate_understanding(events_summary, existing)

    # 写入 .md 文件
    write_about_you(user_id, new_text)

    # 更新 profile_data JSON（可选，作为缓存）
    await _update_profile_data(user_id, new_text)

    logger.info("AI understanding updated", user_id=user_id, chars=len(new_text))
    return new_text


async def _generate_understanding(events_summary: str, existing: str) -> str:
    """调用 LLM 生成画像文本。"""
    from backend.agent.pydantic_agent import _create_model
    from pydantic_ai import Agent

    model = _create_model()

    system_prompt = """你是一个 AI 伴侣的用户画像专家。基于事件数据，生成一段关于用户的综合画像。

## 规则
1. 用第二人称（"你"），像向用户本人介绍他们自己
2. 写 2-3 段自然语言，总长 300-500 字
3. 只包含有证据支撑的观察，不编造
4. 使用"缺席成本测试"：6个月后全新对话中缺少此信息是否导致方向性失误？是→必须包含
5. 优先包含：用户事实（身份背景）、用户偏好（思维方式/价值取向）、关键决策
6. 不包含：可推导信息、临时状态、技术操作细节
7. 直接输出画像文本，不要加标题或分隔线"""

    existing_section = ""
    if existing and len(existing) > 20:
        existing_section = f"\n## 现有画像（需要在此基础上更新，保持连续性）\n{existing}"

    prompt = f"""## 全部事件数据
{events_summary}
{existing_section}

请生成/更新用户画像。"""

    agent = Agent(model=model, output_type=str, system_prompt=system_prompt, retries=1)
    result = await agent.run(prompt)
    return result.output


async def _update_profile_data(user_id: str, about_you: str) -> None:
    """更新 UserProfile.profile_data 中的 ai_understanding 字段。"""
    from backend.domain.models.user import UserProfile
    from sqlalchemy import select

    async with get_async_session_maker()() as db:
        stmt = select(UserProfile).where(UserProfile.user_id == user_id)
        result = await db.execute(stmt)
        profile = result.scalar_one_or_none()
        if profile:
            data = dict(profile.profile_data or {})
            data["ai_understanding"] = about_you
            data["ai_understanding_updated_at"] = datetime.now(UTC).isoformat()
            profile.profile_data = data
            await db.commit()


async def detect_patterns(user_id: str) -> list[dict]:
    """从同类事件中提取模式洞察。"""
    # 1. 统计各类事件数量
    # 2. 当某类 >= 3 条时，调用 LLM 提炼模式
    # 3. 返回模式列表
    # 具体实现参照上方 prompt 设计
    ...


async def get_about_you_data(user_id: str) -> AboutYouData:
    """读取完整的画像数据（关于你 + 模式 + 此刻 + 时间线）。"""
    from backend.memory.markdown import read_about_you

    about_you_text = read_about_you(user_id)

    # 从 profile_data 读取缓存
    patterns = []
    updated_at = ""
    async with get_async_session_maker()() as db:
        from backend.domain.models.user import UserProfile
        stmt = select(UserProfile).where(UserProfile.user_id == user_id)
        result = await db.execute(stmt)
        profile = result.scalar_one_or_none()
        if profile and profile.profile_data:
            data = profile.profile_data
            patterns = data.get("patterns", [])
            updated_at = data.get("ai_understanding_updated_at", "")

    # 构建"此刻"：最近 status_changed + goal_updated
    now_status = await _get_current_status(user_id)

    # 构建"时间线"：全部 events 按时间倒序
    journey = await _get_journey(user_id)

    return AboutYouData(
        about_you=about_you_text,
        updated_at=updated_at,
        patterns=patterns,
        now_status=now_status,
        journey=journey,
    )


async def _get_current_status(user_id: str) -> dict:
    """读取最新状态（status_changed + goal_updated 最近各 3 条）。"""
    from backend.memory.events_merger import merge_dict_events, load_payload

    async with get_async_session_maker()() as db:
        stmt = (
            select(GrowthEvent)
            .where(
                GrowthEvent.user_id == user_id,
                GrowthEvent.event_type.in_(["status_changed", "goal_updated"]),
            )
            .order_by(GrowthEvent.created_at.desc())
            .limit(10)
        )
        result = await db.execute(stmt)
        events = list(result.scalars().all())

    return merge_dict_events(events)


async def _get_journey(user_id: str) -> list[dict]:
    """构建成长时间线。"""
    async with get_async_session_maker()() as db:
        stmt = (
            select(GrowthEvent)
            .where(
                GrowthEvent.user_id == user_id,
                GrowthEvent.event_type.notin_(["profile_updated", "document_uploaded"]),
            )
            .order_by(GrowthEvent.created_at.desc())
            .limit(30)
        )
        result = await db.execute(stmt)
        events = list(result.scalars().all())

    items = []
    for event in events:
        content = ""
        if event.payload_json:
            try:
                p = json.loads(event.payload_json)
                if isinstance(p, dict):
                    content = (
                        p.get("content")
                        or p.get("value")
                        or p.get("memory_md")
                        or p.get("description")
                        or p.get("title", "")
                    )
            except json.JSONDecodeError:
                pass
        if not content:
            content = f"{event.event_type}"
        items.append({
            "id": str(event.id),
            "type": event.event_type,
            "content": content[:200],
            "date": event.created_at.isoformat() if event.created_at else None,
        })
    return items
```

### 5.2 修改 `backend/memory/markdown.py`

追加 `read_about_you` / `write_about_you` / `read_patterns` / `write_patterns` 函数：

```python
# 在现有函数后面追加

def read_about_you(user_id: str) -> str:
    path = memory_dir(user_id) / "about_you.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_about_you(user_id: str, content: str) -> None:
    ensure_memory_dirs(user_id)
    _write_md_file_safe(
        str(memory_dir(user_id) / "about_you.md"),
        content,
        max_chars=MD_CHAR_LIMITS.get("about_you", 2000),
    )


def read_patterns(user_id: str) -> str:
    path = memory_dir(user_id) / "patterns.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_patterns(user_id: str, content: str) -> None:
    ensure_memory_dirs(user_id)
    _write_md_file_safe(
        str(memory_dir(user_id) / "patterns.md"),
        content,
        max_chars=MD_CHAR_LIMITS.get("patterns", 2000),
    )
```

### 5.3 修改 `backend/memory/constants.py`

追加字符限制：

```python
MD_CHAR_LIMITS: dict[str, int] = {
    "memory": 4000,
    "skills": 3000,
    "experiences": 5000,
    "about_you": 2000,    # 新增
    "patterns": 2000,     # 新增
}
```

### 5.4 修改 `backend/memory/snapshot.py`

L0 固定块改造：**优先使用 about_you.md 自然语言，降级到字段拼接**。

在 `_build_fixed_block` 函数之前插入新函数，修改 `build_snapshot` 调用：

```python
# 新增函数
def _build_fixed_block_v2(
    user_id: str,
    profile: dict,
    goals: dict,
    skills: dict,
    preferences: dict,
) -> str:
    """L0 固定块 v2：优先使用 AI 综合画像，降级到字段拼接。"""
    from backend.memory.markdown import read_about_you

    about_you = read_about_you(user_id)
    if about_you and len(about_you.strip()) > 50:
        # 有 AI 综合画像时，直接使用
        return f"## AI 对你的理解\n{about_you.strip()}"

    # 降级：使用旧的字段拼接
    return _build_fixed_block(profile, goals, skills, preferences)


# 修改 build_snapshot 中的调用
# 原来是：
#   fixed_block = _build_fixed_block(profile, goals, skills, preferences)
# 改为：
#   fixed_block = _build_fixed_block_v2(user_id, profile, goals, skills, preferences)
```

### 5.5 修改 `backend/memory/facade.py`

在 `flush_projections` 和 `sync_projections` 中追加异步画像更新：

```python
# LumenMemory 类新增方法
async def _update_understanding(self, user_id: str) -> None:
    """后台异步：更新 AI 综合画像。"""
    try:
        from backend.memory.understanding import update_ai_understanding, detect_patterns
        await update_ai_understanding(user_id)
        # patterns 检测较重，可以降频（如每 5 次对话检测一次）
        # await detect_patterns(user_id)
    except Exception as exc:
        logger.warning("AI understanding update skipped", user_id=user_id, error=str(exc))
```

然后在 `flush_projections` 和 `sync_projections` 末尾追加调用：

```python
# 在 cognee task 后面追加
task2 = asyncio.create_task(self._update_understanding(user_id))
_background_tasks.add(task2)
task2.add_done_callback(_background_tasks.discard)
```

### 5.6 新增 API 端点

在 `backend/api/routers/memory.py` 追加：

```python
class AboutYouResponse(BaseModel):
    about_you: str = ""
    updated_at: str = ""
    patterns: list[dict[str, Any]] = Field(default_factory=list)
    now_status: dict[str, str] = Field(default_factory=dict)
    journey: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/understanding", response_model=AboutYouResponse)
async def get_ai_understanding(user_id: str = Query("demo_user")) -> AboutYouResponse:
    """获取 AI 综合画像（关于你 + 模式洞察 + 此刻状态 + 时间线）。"""
    _validate_user_id(user_id)
    try:
        from backend.memory.understanding import get_about_you_data
        data = await get_about_you_data(user_id)
        return AboutYouResponse(
            about_you=data.about_you,
            updated_at=data.updated_at,
            patterns=data.patterns,
            now_status=data.now_status,
            journey=data.journey,
        )
    except Exception as exc:
        logger.error("AI understanding read failed: %s", exc)
        return AboutYouResponse()


@router.post("/understanding/refresh")
async def refresh_ai_understanding(user_id: str = Query("demo_user")) -> dict:
    """手动触发 AI 画像重新生成。"""
    _validate_user_id(user_id)
    try:
        from backend.memory.understanding import update_ai_understanding
        text = await update_ai_understanding(user_id)
        return {"message": "画像已更新", "chars": len(text)}
    except Exception as exc:
        logger.error("AI understanding refresh failed: %s", exc)
        raise HTTPException(status_code=500, detail="画像更新失败") from exc


@router.post("/understanding/correct")
async def correct_ai_understanding(
    body: dict[str, str],
    user_id: str = Query("demo_user"),
) -> dict:
    """用户手动纠正 AI 画像文本。"""
    _validate_user_id(user_id)
    corrected_text = body.get("text", "")
    if not corrected_text:
        raise HTTPException(status_code=400, detail="纠正内容不能为空")
    from backend.memory.markdown import write_about_you
    write_about_you(user_id, corrected_text)
    # 同步更新 profile_data 缓存
    from backend.memory.understanding import _update_profile_data
    await _update_profile_data(user_id, corrected_text)
    return {"message": "已更新", "chars": len(corrected_text)}
```

### 5.7 注册路由

检查 `backend/api/routers/__init__.py` 或 `backend/main.py`，确保 memory router 已注册（应该已有）。

## 6. 前端逐文件改动

### 6.1 新增 API 调用 — `src/lib/api.ts`

在文件末尾追加：

```typescript
// ── AI Understanding (画像层新设计) ──

export type AboutYouResponse = {
  about_you: string;
  updated_at: string;
  patterns: Array<{
    insight: string;
    category: string;
    evidence_count: number;
    first_seen: string;
    updated_at: string;
  }>;
  now_status: Record<string, string>;
  journey: Array<{
    id: string;
    type: string;
    content: string;
    date: string | null;
  }>;
};

export function getAIUnderstanding(): Promise<AboutYouResponse> {
  return http<AboutYouResponse>(
    `/api/memory/understanding?user_id=${encodeURIComponent(cachedUserId)}`,
  );
}

export function refreshAIUnderstanding(): Promise<{ message: string; chars: number }> {
  return http<{ message: string; chars: number }>(
    `/api/memory/understanding/refresh?user_id=${encodeURIComponent(cachedUserId)}`,
    { method: "POST" },
  );
}

export function correctAIUnderstanding(text: string): Promise<{ message: string; chars: number }> {
  return http<{ message: string; chars: number }>(
    `/api/memory/understanding/correct?user_id=${encodeURIComponent(cachedUserId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    },
  );
}
```

### 6.2 重构 `src/pages/Profile.tsx`

完整替换。保留的关键逻辑：
- `Card` / `EditButton` / `SaveButton` / `EmptyState` 组件（保留）
- `loadData` 机制（改为加载 understanding + structured profile 双数据源）
- 编辑/保存简历数据的逻辑（折叠到底部保留）
- 上传简历拖拽逻辑（保留）

新布局：

```
┌─────────────────────────────────────────┐
│  AI 眼中的你              [纠正] [重置]   │
│  这是 AI 从对话中拼凑出的画像              │
├─────────────────────────────────────────┤
│                                         │
│  ┌─── 关于你 ──────────────────── [刷新] ┐ │
│  │  {about_you} 自然语言文本              │ │
│  │  {更新于 X 小时前}                     │ │
│  └────────────────────────────────────┘ │
│                                         │
│  ┌─── AI 注意到的 ─────────────────────┐ │
│  │  洞察卡片列表（icon + 文本）           │ │
│  └────────────────────────────────────┘ │
│                                         │
│  ┌─── 此刻 ───────────────────────────┐ │
│  │  状态 key: value 列表                 │ │
│  └────────────────────────────────────┘ │
│                                         │
│  ┌─── 你走过的路 ─────────────────────┐ │
│  │  时间线组件                           │ │
│  └────────────────────────────────────┘ │
│                                         │
│  ┌─── 简历数据 ▼ ────────────────────┐ │
│  │  （折叠，点击展开）                    │ │
│  │  基础信息 / 技能 / 经历 编辑          │ │
│  └────────────────────────────────────┘ │
│                                         │
│  [上传简历]                              │
└─────────────────────────────────────────┘
```

关键实现点：

```tsx
// 数据加载
const [understanding, setUnderstanding] = useState<AboutYouResponse | null>(null);
const [structuredData, setStructuredData] = useState<StructuredProfile | null>(null);
const [showResumeData, setShowResumeData] = useState(false);

const loadData = useCallback(async () => {
  setLoading(true);
  try {
    // 并行加载两个数据源
    const [under, profile] = await Promise.all([
      getAIUnderstanding(),
      getStructuredProfile(),
    ]);
    setUnderstanding(under);
    setStructuredData(profile);
  } catch (e) {
    setError((e as Error).message);
  } finally {
    setLoading(false);
  }
}, []);

// "纠正"模式：内联编辑 about_you 文本
const [editingAboutYou, setEditingAboutYou] = useState(false);
const [editAboutYouText, setEditAboutYouText] = useState("");

// "关于你"区块渲染
function renderAboutYou() {
  if (!understanding?.about_you) {
    return (
      <Card title="关于你">
        <EmptyState
          message="AI 还没有形成对你的理解"
          hint="和 AI 多聊聊，它会逐渐了解你"
        />
      </Card>
    );
  }

  return (
    <Card
      title="关于你"
      action={
        <div className="flex gap-xs">
          <button onClick={handleRefreshAboutYou} className="text-xs text-ink ...">
            刷新
          </button>
          {editingAboutYou ? (
            <>
              <button onClick={() => setEditingAboutYou(false)} className="text-xs ...">取消</button>
              <button onClick={handleSaveCorrection} className="text-xs ...">保存</button>
            </>
          ) : (
            <button onClick={handleStartCorrection} className="text-xs ...">纠正</button>
          )}
        </div>
      }
    >
      {editingAboutYou ? (
        <textarea
          value={editAboutYouText}
          onChange={(e) => setEditAboutYouText(e.target.value)}
          rows={8}
          className="w-full bg-bg border border-border-soft rounded-md px-md py-sm text-sm text-text ..."
        />
      ) : (
        <div className="space-y-sm">
          {understanding.about_you.split("\n\n").map((para, i) => (
            <p key={i} className="text-sm text-text leading-relaxed">{para}</p>
          ))}
          {understanding.updated_at && (
            <p className="text-xs text-text-subtle">
              更新于 {formatRelativeTime(understanding.updated_at)}
            </p>
          )}
        </div>
      )}
    </Card>
  );
}

// "AI 注意到的"区块
function renderPatterns() {
  if (!understanding?.patterns?.length) return null;
  return (
    <Card title="AI 注意到的">
      <div className="space-y-sm">
        {understanding.patterns.map((p, i) => (
          <div key={i} className="flex items-start gap-sm p-sm bg-bg rounded-lg border border-border-soft">
            <span className="text-base">{_categoryIcon(p.category)}</span>
            <p className="text-sm text-text leading-relaxed">{p.insight}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}

// "此刻"区块
function renderNow() {
  const status = understanding?.now_status;
  if (!status || Object.keys(status).length === 0) return null;
  return (
    <Card title="此刻">
      <div className="space-y-xs">
        {Object.entries(status).map(([k, v]) => (
          <div key={k} className="flex items-baseline gap-xs">
            <span className="text-xs text-text-subtle shrink-0">{k}</span>
            <span className="text-sm text-text">{v}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

// "你走过的路"区块 — 时间线
function renderJourney() {
  if (!understanding?.journey?.length) return null;
  return (
    <Card title="你走过的路">
      <div className="relative pl-lg">
        {/* 竖线 */}
        <div className="absolute left-sm top-0 bottom-0 w-px bg-border-soft" />
        <div className="space-y-md">
          {understanding.journey.map((item, i) => (
            <div key={item.id} className="relative">
              {/* 圆点 */}
              <div className="absolute -left-lg top-1 w-2 h-2 rounded-full bg-ink/50 border border-ink" />
              <div>
                <p className="text-sm text-text">{item.content}</p>
                <p className="text-xs text-text-subtle mt-0.5">
                  {item.date ? formatDate(item.date) : ''}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

// 简历数据折叠区块
function renderResumeData() {
  return (
    <div className="mb-lg">
      <button
        onClick={() => setShowResumeData(!showResumeData)}
        className="w-full flex items-center justify-between px-md py-sm border border-border-soft rounded-xl bg-surface hover:bg-surface-elevated transition-colors"
      >
        <span className="text-sm font-medium text-text">简历数据</span>
        <span className="text-xs text-text-subtle">{showResumeData ? '收起' : '展开'}</span>
      </button>
      {showResumeData && (
        <div className="mt-sm space-y-lg">
          {/* 复用现有的基础信息/技能/经历卡片 */}
        </div>
      )}
    </div>
  );
}
```

## 7. 实现顺序

| 步骤 | 文件 | 改动类型 | 估时 |
|------|------|---------|------|
| 1 | `backend/memory/constants.py` | 追加 2 个常量 | 1 min |
| 2 | `backend/memory/markdown.py` | 追加 4 个读写函数 | 5 min |
| 3 | `backend/memory/understanding.py` | 新建，核心文件 | 30 min |
| 4 | `backend/memory/snapshot.py` | 改 `_build_fixed_block` → `_build_fixed_block_v2`，改 `build_snapshot` 调用 | 10 min |
| 5 | `backend/memory/facade.py` | 追加 `_update_understanding`，改 `flush_projections` + `sync_projections` | 10 min |
| 6 | `backend/api/routers/memory.py` | 追加 3 个端点 + 1 个 Response Model | 15 min |
| 7 | `src/lib/api.ts` | 追加 3 个函数 + 1 个类型 | 5 min |
| 8 | `src/pages/Profile.tsx` | 重构：四区块布局 + 简历折叠 | 45 min |

**推荐实现路径**：先完成后端（步骤 1-6），验证 API 正常后再做前端（步骤 7-8）。

## 8. 验证清单

后端验证：
- [ ] `GET /api/memory/understanding` 返回正确结构
- [ ] `POST /api/memory/understanding/refresh` 触发 LLM 生成画像
- [ ] `POST /api/memory/understanding/correct` 更新画像文本
- [ ] 对话结束后 `about_you.md` 自动更新
- [ ] Agent system prompt 中 L0 固定块使用自然语言画像
- [ ] 旧的字段拼接降级路径正常工作

前端验证：
- [ ] 四区块正确渲染：关于你 / AI 注意到的 / 此刻 / 你走过的路
- [ ] "纠正"按钮进入编辑模式，保存后更新
- [ ] "刷新"按钮触发重新生成
- [ ] 简历数据折叠展开正常
- [ ] 无数据时显示正确空态
- [ ] 上传简历功能不受影响

## 9. 已知风险与缓解

| 风险 | 缓解 |
|------|------|
| LLM 调用失败导致画像为空 | 降级到旧字段拼接；前端显示"AI 尚未形成理解"空态 |
| 频繁 LLM 调用消耗 token | 对话后异步调用 + 缓存；手动刷新才触发 |
| 画像内容不准确 | 用户可随时"纠正"；修改会写入 .md 覆盖 |
| events 过多导致 prompt 过长 | 限制最多取 50 条事件摘要 |
| `_create_model()` 依赖配置 | 复用现有 Agent 创建逻辑，依赖用户已配置的 API Key |
