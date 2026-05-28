"""AI 综合画像生成器。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from lib.memory.markdown import AsyncMarkdownStore
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


async def _get_profile_text(user_id: str) -> tuple[str, int]:
    """读取 MEMORY.md 作为 LLM 画像生成的输入。

    Returns:
        (memory_md_content, char_count)
    """
    store = AsyncMarkdownStore()
    content = await store.read_memory(user_id)
    if not content.strip():
        return "", 0
    return content, len(content)


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
    store = AsyncMarkdownStore()

    if _is_debounced(user_id):
        return await store.read_about_you(user_id)

    # 创建 protected task 并在完成后自动清理
    async with _PENDING_LOCK:
        # 双重检查：获取锁后可能已有新任务注册
        if _is_debounced(user_id):
            return await store.read_about_you(user_id)
        task = asyncio.create_task(_do_update_understanding(user_id))
        _PENDING_TASKS[user_id] = task

    try:
        return await task
    except asyncio.CancelledError:
        logger.debug("AI understanding cancelled", user_id=user_id)
        return await store.read_about_you(user_id)
    finally:
        async with _PENDING_LOCK:
            if _PENDING_TASKS.get(user_id) is task:
                del _PENDING_TASKS[user_id]


async def _do_update_understanding(user_id: str) -> str:
    """执行实际的 LLM 画像生成（由 update_ai_understanding 的 protected task 调用）。"""
    store = AsyncMarkdownStore()

    content, _ = await _get_profile_text(user_id)
    existing = await store.read_about_you(user_id)

    if not content.strip():
        return existing

    try:
        raw_output = await _generate_understanding(content, existing)
    except Exception as exc:
        logger.warning("AI understanding generation failed", user_id=user_id, error=str(exc))
        return existing

    # 分离画像文本和模式洞察
    about_you_text, patterns = _parse_understanding_output(raw_output)

    # 保留现有 frontmatter（如果有）
    from lib.memory.markdown import _parse_frontmatter

    existing_frontmatter, _ = _parse_frontmatter(existing)
    if existing_frontmatter:
        from lib.memory.markdown import _dump_frontmatter

        about_you_text = "---\n" + _dump_frontmatter(existing_frontmatter) + "\n---\n\n" + about_you_text

    await store.write_about_you(user_id, about_you_text)
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
    """调用 LLM 基于 memory.md 结构化画像生成 about_you 自然语言文本。"""
    from pydantic_ai import Agent

    from core.agent import _lumen_agent

    model = _lumen_agent._create_model()

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

    prompt = f"""## 用户画像数据（来自 MEMORY.md）
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
    from sqlalchemy import select

    from core.db import get_async_session_maker
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

    updated_at 从 USER.md 元数据注释中解析，不再依赖 DB 双写。
    intents/now_status/journey 在 Hermes-Pure 架构下暂不填充（前端已隐藏）。
    """
    import re as _re

    store = AsyncMarkdownStore()
    about_you_text = await store.read_about_you(user_id)

    # 从 about_you.md 的 meta 注释中提取 generated_at 时间戳
    updated_at = ""
    if about_you_text:
        meta_match = _re.match(r"<!-- lumen-meta:.*?generated_at=([^\s>]+)", about_you_text)
        if meta_match:
            updated_at = meta_match.group(1)

    # 从 profile_data 读取模式洞察
    patterns: list[dict] = []
    try:
        from sqlalchemy import select

        from core.db import get_async_session_maker
        from lib.profile.models import UserProfile

        async with get_async_session_maker()() as db:
            stmt = select(UserProfile.profile_data).where(UserProfile.user_id == user_id)
            result = await db.execute(stmt)
            profile_data = result.scalar()
            if profile_data and isinstance(profile_data, dict):
                stored_patterns = profile_data.get("patterns")
                if isinstance(stored_patterns, list):
                    patterns = stored_patterns
    except Exception:
        pass

    return AboutYouData(
        about_you=about_you_text,
        updated_at=updated_at,
        patterns=patterns,
        intents=[],  # Hermes-Pure: 暂不填充
        now_status={},  # Hermes-Pure: 暂不填充
        journey=[],  # Hermes-Pure: 暂不填充
    )
