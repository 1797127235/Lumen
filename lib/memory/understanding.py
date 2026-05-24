"""AI 综合画像生成器。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from core.db import get_async_session_maker
from lib.memory.models import GrowthEvent
from shared.logging import get_logger

logger = get_logger(__name__)

# ── 防抖：同一 user_id 5 分钟内、或已有进行中的任务时不重复触发 ──
_PENDING_TASKS: dict[str, asyncio.Task] = {}
_LAST_UPDATE: dict[str, datetime] = {}
_DEBOUNCE_SECONDS = 300
_PENDING_LOCK = asyncio.Lock()


@dataclass
class AboutYouData:
    about_you: str = ""
    updated_at: str = ""
    patterns: list[dict] = field(default_factory=list)
    intents: list[dict] = field(default_factory=list)
    now_status: dict = field(default_factory=dict)
    journey: list[dict] = field(default_factory=list)


async def _get_profile_text(user_id: str) -> tuple[str, int, int]:
    """读取 memory.md 作为 LLM 画像生成的输入。

    替代原来的 _get_all_events_summary（逐事件读取），改为读取已合并的 memory.md。
    memory.md 是 project_user_to_md 的产出，包含去重、合并后的结构化画像数据。

    Returns:
        (memory_md_content, char_count, event_count)
    """
    from sqlalchemy import func as _func

    from lib.memory.markdown import read_memory

    content = read_memory(user_id)
    if not content.strip():
        return "", 0, 0

    async with get_async_session_maker()() as db:
        result = await db.execute(select(_func.count(GrowthEvent.id)).where(GrowthEvent.user_id == user_id))
        count = result.scalar() or 0

    return content, len(content), count


def _is_debounced(user_id: str) -> bool:
    """检查是否应该跳过本次触发（并发防抖 + 时间窗口防抖）。"""
    existing_task = _PENDING_TASKS.get(user_id)
    if existing_task is not None and not existing_task.done():
        logger.debug("AI understanding already in progress", user_id=user_id)
        return True

    now = datetime.now(UTC)
    last = _LAST_UPDATE.get(user_id)
    if last and (now - last) < timedelta(seconds=_DEBOUNCE_SECONDS):
        logger.debug("AI understanding debounced", user_id=user_id, last=last.isoformat())
        return True

    return False


async def update_ai_understanding(user_id: str) -> str:
    """生成/更新 AI 综合画像。返回画像文本。

    防抖：同一 user_id 已有进行中的任务或最近更新过时不重复触发。
    成功后记录时间戳，失败不留时间戳（允许立即重试）。
    """
    from lib.memory.markdown import read_about_you

    if _is_debounced(user_id):
        return read_about_you(user_id)

    # 创建 protected task 并在完成后自动清理
    async with _PENDING_LOCK:
        # 双重检查：获取锁后可能已有新任务注册
        if _is_debounced(user_id):
            return read_about_you(user_id)
        task = asyncio.create_task(_do_update_understanding(user_id))
        _PENDING_TASKS[user_id] = task

    try:
        return await task
    except asyncio.CancelledError:
        logger.debug("AI understanding cancelled", user_id=user_id)
        return read_about_you(user_id)
    finally:
        async with _PENDING_LOCK:
            if _PENDING_TASKS.get(user_id) is task:
                del _PENDING_TASKS[user_id]


async def _do_update_understanding(user_id: str) -> str:
    """执行实际的 LLM 画像生成（由 update_ai_understanding 的 protected task 调用）。"""
    from lib.memory.markdown import read_about_you, write_about_you

    content, _, event_count = await _get_profile_text(user_id)
    existing = read_about_you(user_id)

    if not content.strip():
        return existing

    try:
        raw_output = await _generate_understanding(content, existing)
    except Exception as exc:
        logger.warning("AI understanding generation failed", user_id=user_id, error=str(exc))
        return existing

    # 分离画像文本和模式洞察
    about_you_text, patterns = _parse_understanding_output(raw_output)

    write_about_you(user_id, about_you_text, event_count=event_count)
    await _update_profile_data(user_id, about_you_text, patterns)

    # 只在成功后才写入时间戳（允许失败立即重试）
    _LAST_UPDATE[user_id] = datetime.now(UTC)

    logger.info(
        "AI understanding updated",
        user_id=user_id,
        chars=len(about_you_text),
        patterns=len(patterns),
    )
    return about_you_text


async def _generate_understanding(profile_text: str, existing: str) -> str:
    """调用 LLM 基于 memory.md 结构化画像生成 about_you 自然语言文本。

    输入从原始事件列表改为 memory.md（project_user_to_md 的产出），
    与结构化投影形成上下游关系，消除独立读取事件的重复开销。
    """
    from pydantic_ai import Agent

    from core.agent import create_model

    model = create_model()

    system_prompt = """你是一个 AI 伙伴的用户画像专家。基于用户的画像数据（Markdown 格式），生成一段关于用户的综合画像 + 模式洞察。

## 输出格式
先输出画像文本，然后换行，然后输出一行分隔符 `---PATTERNS---`，然后输出 JSON 数组。

示例：
你是一位...

---PATTERNS---
[
  {"insight": "你每次面临重大选择前都会焦虑2-3天", "category": "decision_pattern", "evidence_count": 4},
  {"insight": "你提到独处时总是在晚上", "category": "time_preference", "evidence_count": 3}
]

## 规则
1. 画像文本用第二人称（"你"），像向用户本人介绍他们自己
2. 画像写 2-3 段自然语言，总长 300-500 字
3. 只包含有证据支撑的观察，不编造
4. 使用"缺席成本测试"：6个月后全新对话中缺少此信息是否导致方向性失误？是→必须包含
5. 优先包含：用户事实（身份背景）、用户偏好（思维方式/价值取向）、关键决策
6. 忽略标记为"（待填写）"的字段——那是占位符，不是真实数据
7. 模式洞察：从画像数据中提炼 3-5 条跨维度模式，每条用一句话概括
8. category 只能从以下选择：time_preference, learning_style, decision_pattern, value_orientation, communication_style, emotional_pattern, social_style, energy_pattern
9. evidence_count 是基于事件数量的合理估算（1-10）"""

    existing_section = ""
    if existing and len(existing) > 20:
        existing_section = f"\n## 现有画像（需要在此基础上更新，保持连续性）\n{existing}"

    prompt = f"""## 用户画像数据（来自 memory.md）
{profile_text}
{existing_section}

请生成/更新用户画像。"""

    agent = Agent(model=model, output_type=str, system_prompt=system_prompt, retries=1)
    result = await agent.run(prompt)
    return result.output


def _parse_understanding_output(raw: str) -> tuple[str, list[dict]]:
    """从 LLM 输出中分离画像文本和模式洞察 JSON。"""
    if "---PATTERNS---" not in raw:
        return raw.strip(), []

    parts = raw.split("---PATTERNS---", 1)
    about_you = parts[0].strip()
    patterns_json = parts[1].strip()

    try:
        patterns = json.loads(patterns_json)
        if isinstance(patterns, list):
            return about_you, patterns
    except json.JSONDecodeError:
        logger.warning("Failed to parse patterns JSON", raw=patterns_json[:200])

    return about_you, []


async def _update_profile_data(user_id: str, about_you: str, patterns: list[dict] | None = None) -> None:
    """更新 UserProfile.profile_data 中的 ai_understanding 和 patterns 字段。"""
    from lib.profile.models import UserProfile

    async with get_async_session_maker()() as db:
        stmt = select(UserProfile).where(UserProfile.user_id == user_id)
        result = await db.execute(stmt)
        profile = result.scalar_one_or_none()
        if profile:
            data = dict(profile.profile_data or {})
            data["ai_understanding"] = about_you
            data["ai_understanding_updated_at"] = datetime.now(UTC).isoformat()
            if patterns is not None:
                data["patterns"] = patterns
            profile.profile_data = data
            await db.commit()


async def get_about_you_data(user_id: str) -> AboutYouData:
    """读取完整的画像数据（关于你 + 模式 + 此刻 + 时间线）。

    updated_at 从 about_you.md 元数据注释中解析，不再依赖 DB 双写。
    """
    import re as _re

    from lib.memory.markdown import read_about_you

    about_you_text = read_about_you(user_id)

    # 从 about_you.md 的 meta 注释中提取 generated_at 时间戳
    updated_at = ""
    if about_you_text:
        meta_match = _re.match(r"<!-- lumen-meta:.*?generated_at=([^\s>]+)", about_you_text)
        if meta_match:
            updated_at = meta_match.group(1)

    now_status = await _get_current_status(user_id)
    journey = await _get_journey(user_id)

    # 从 profile_data 读取模式洞察
    patterns: list[dict] = []
    try:
        async with get_async_session_maker()() as db:
            from lib.profile.models import UserProfile

            stmt = select(UserProfile.profile_data).where(UserProfile.user_id == user_id)
            result = await db.execute(stmt)
            profile_data = result.scalar()
            if profile_data and isinstance(profile_data, dict):
                stored_patterns = profile_data.get("patterns")
                if isinstance(stored_patterns, list):
                    patterns = stored_patterns
    except Exception:
        pass

    intents = await _extract_intents(user_id)

    return AboutYouData(
        about_you=about_you_text,
        updated_at=updated_at,
        patterns=patterns,
        intents=intents,
        now_status=now_status,
        journey=journey,
    )


async def _extract_intents(user_id: str) -> list[dict]:
    """从 value_surfaced / interest_observed 事件中提取用户表达的意图/目标。

    匹配关键词：我想、我希望、我打算、我要、我计划、我想做。
    返回按最近提及时间排序的意图列表。
    """
    _INTENT_KEYWORDS = ["我想", "我希望", "我打算", "我要", "我计划", "我想做"]
    _MIN_CHARS = 4

    async with get_async_session_maker()() as db:
        stmt = (
            select(GrowthEvent)
            .where(
                GrowthEvent.user_id == user_id,
                GrowthEvent.event_type.in_(["value_surfaced", "interest_observed"]),
            )
            .order_by(GrowthEvent.created_at.desc())
            .limit(200)
        )
        result = await db.execute(stmt)
        events = list(result.scalars().all())

    # 提取所有意图文本并去重
    seen: set[str] = set()
    intents: list[dict] = []

    for event in events:
        if not event.payload_json:
            continue
        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        text = (
            payload.get("content")
            or payload.get("value")
            or payload.get("description")
            or payload.get("memory_md")
            or ""
        )
        if not text or len(text) < _MIN_CHARS:
            continue

        # 查找意图关键词
        for kw in _INTENT_KEYWORDS:
            idx = text.find(kw)
            if idx == -1:
                continue
            # 提取从关键词到句号/换行/文本结束的片段
            start = idx
            end_candidates = [
                text.find("，", start + len(kw)),
                text.find("。", start + len(kw)),
                text.find("\n", start + len(kw)),
                text.find("；", start + len(kw)),
            ]
            ends = [e for e in end_candidates if e != -1]
            end = min(ends) + 1 if ends else len(text)
            snippet = text[start:end].strip()
            if len(snippet) < _MIN_CHARS:
                continue

            # 去重：基于前 20 字符
            dedup_key = snippet[:20]
            if dedup_key in seen:
                # 更新已有意图的最后提及时间
                for item in intents:
                    if item["text"][:20] == dedup_key:
                        item["mention_count"] = item.get("mention_count", 1) + 1
                        if event.created_at:
                            item["last_mentioned_at"] = event.created_at.isoformat()
                        break
                continue

            seen.add(dedup_key)
            category = "goal" if "计划" in snippet or "打算" in snippet else "wish"
            intents.append(
                {
                    "text": snippet,
                    "category": category,
                    "first_mentioned_at": event.created_at.isoformat() if event.created_at else "",
                    "last_mentioned_at": event.created_at.isoformat() if event.created_at else "",
                    "mention_count": 1,
                }
            )

    # 按最近提及时间倒序，最多返回 10 条
    intents.sort(key=lambda x: x.get("last_mentioned_at", ""), reverse=True)
    return intents[:10]


async def _get_current_status(user_id: str) -> dict:
    """读取最新状态事件（emotional_pattern + value_surfaced 最近各 10 条）。"""
    from lib.memory.events_merger import merge_dict_events

    async with get_async_session_maker()() as db:
        stmt = (
            select(GrowthEvent)
            .where(
                GrowthEvent.user_id == user_id,
                GrowthEvent.event_type.in_(["emotional_pattern", "value_surfaced"]),
            )
            .order_by(GrowthEvent.created_at.desc())
            .limit(10)
        )
        result = await db.execute(stmt)
        events = list(result.scalars().all())

    return merge_dict_events(events)


async def _get_journey(user_id: str) -> list[dict]:
    """构建成长时间线——聚合原始事件为有意义的时间节点。"""

    _TYPE_LABELS: dict[str, str] = {
        "interest_observed": "发现新兴趣",
        "value_surfaced": "价值观浮现",
        "preference_learned": "偏好发现",
        "emotional_pattern": "情绪洞察",
        "significant_moment": "重要经历",
        "decision_made": "关键决策",
        "reflection_added": "自我反思",
        "contradiction_noted": "矛盾观察",
        "relationship_noted": "关系记录",
    }

    async with get_async_session_maker()() as db:
        stmt = (
            select(GrowthEvent)
            .where(
                GrowthEvent.user_id == user_id,
                GrowthEvent.event_type.notin_(["profile_updated"]),
            )
            .order_by(GrowthEvent.created_at.asc())
            .limit(50)
        )
        result = await db.execute(stmt)
        events = list(result.scalars().all())

    def _extract_content(event: GrowthEvent) -> str:
        if not event.payload_json:
            return ""
        try:
            p = json.loads(event.payload_json)
            if not isinstance(p, dict):
                return ""
            return (
                p.get("content")
                or p.get("value")
                or p.get("memory_md")
                or p.get("description")
                or p.get("title")
                or p.get("name")
                or ""
            )
        except json.JSONDecodeError:
            return ""

    # 按类型+时间窗口聚合（1小时内同类型合并）
    aggregated: list[dict] = []
    AGGREGATION_WINDOW_SEC = 3600

    for event in events:
        label = _TYPE_LABELS.get(event.event_type, event.event_type.replace("_", " ").title())
        content = _extract_content(event)

        # 尝试聚合到上一个同类型条目
        if aggregated:
            last = aggregated[-1]
            time_diff = (
                (event.created_at - last["_latest_date"]).total_seconds()
                if event.created_at and last["_latest_date"]
                else float("inf")
            )
            if last["type"] == label and time_diff < AGGREGATION_WINDOW_SEC:
                last["count"] += 1
                if content and content not in last.get("_contents", []):
                    last["_contents"].append(content)
                last["_latest_date"] = event.created_at
                continue

        # 新开一条
        entry: dict = {
            "id": str(event.id),
            "type": label,
            "content": content[:120] if content else f"新的{label}",
            "date": event.created_at.isoformat() if event.created_at else None,
            "_latest_date": event.created_at,
            "count": 1,
            "_contents": [content] if content else [],
        }
        aggregated.append(entry)

    # 清理内部字段 + 聚合文案
    for item in aggregated:
        item.pop("_latest_date", None)
        item.pop("_contents", None)
        count = item.pop("count", 1)
        if count > 1:
            item["content"] = f"新增了 {count} 项{item['type']}"

    return aggregated[:12]
