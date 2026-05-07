"""Agent 系统提示快照 — 从 SQLite 直接构建结构化用户画像。

每轮对话开始时调用 build_snapshot()，注入到 Agent 系统提示。
不依赖 .md 文件（.md 只用于前端展示），避免投影延迟导致 Agent 看到空数据。

缓存策略：projection flush/sync/rebuild 时由 facade 触发 invalidate_cache() 失效。
"""

from __future__ import annotations

from collections import defaultdict
from typing import ClassVar

from app.backend.db.base import get_async_session_maker
from app.backend.logging_config import get_logger
from app.backend.memory.constants import MD_CHAR_LIMITS
from app.backend.memory.stores.relational import GrowthEventRepository

logger = get_logger(__name__)

MEMORY_CHAR_LIMIT = MD_CHAR_LIMITS["memory"]
SKILLS_CHAR_LIMIT = MD_CHAR_LIMITS["skills"]
EXPERIENCES_CHAR_LIMIT = MD_CHAR_LIMITS["experiences"]

_static_cache: ClassVar[dict[str, tuple[str, str]]] = {}


def invalidate_cache(user_id: str) -> None:
    """projection flush/sync/rebuild 时调用，使缓存失效。"""
    _static_cache.pop(user_id, None)


def _block(label: str, name: str, content: str) -> str:
    chars = len(content)
    limit = {"memory": MEMORY_CHAR_LIMIT, "skills": SKILLS_CHAR_LIMIT, "experiences": EXPERIENCES_CHAR_LIMIT}.get(
        name, 0
    )
    pct = int(chars / limit * 100) if limit else 0
    header = f"══ {label} [{pct}% — {chars:,}/{limit:,} 字符] ══"
    return f"{header}\n{content.strip()}"


async def build_snapshot(user_id: str) -> str:
    """从 SQLite 直接构建 Agent 系统提示快照。

    查询 growth_events 表合并事件，生成结构化上下文。
    缓存由 invalidate_cache() 管理，projection 触发时失效。
    """
    if user_id in _static_cache:
        return _static_cache[user_id][1]

    async with get_async_session_maker()() as db:
        repo = GrowthEventRepository(db)
        events = await repo.get_all_by_user(user_id)

    if not events:
        result = "【用户画像为空】"
        _static_cache[user_id] = (user_id, result)
        return result

    from app.backend.memory.projections.events_merger import (
        generate_experiences_md,
        generate_memory_md,
        generate_skills_md,
        merge_decision_events,
        merge_dict_events,
        merge_experience_events,
        merge_profile_events,
        merge_skill_events,
    )

    events_by_type: dict[str, list] = defaultdict(list)
    for event in events:
        events_by_type[event.event_type].append(event)

    profile = merge_profile_events(events_by_type.get("profile_updated", []))
    skills = merge_skill_events(events_by_type.get("skill_added", []) + events_by_type.get("skill_level_changed", []))
    experiences = merge_experience_events(events_by_type.get("experience_added", []))
    preferences = merge_dict_events(events_by_type.get("preference_learned", []))
    status = merge_dict_events(events_by_type.get("status_changed", []))
    goals = merge_dict_events(events_by_type.get("goal_updated", []))
    decisions = merge_decision_events(events_by_type.get("decision_made", []))

    memory_md = generate_memory_md(profile, preferences, status, goals, decisions)
    skills_md = generate_skills_md(skills)
    experiences_md = generate_experiences_md(experiences)

    parts = [_block("核心记忆", "memory", memory_md)]
    if skills:
        parts.append(_block("技能", "skills", skills_md))
    if experiences:
        parts.append(_block("经历", "experiences", experiences_md))

    result = "\n\n".join(parts)
    _static_cache[user_id] = (user_id, result)
    return result
