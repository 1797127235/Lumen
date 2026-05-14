"""画像查询与更新服务。"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from backend.core.db import get_async_session_maker
from backend.core.logging import get_logger
from backend.modules.memory.events_merger import (
    merge_decision_events,
    merge_dict_events,
    merge_experience_events,
    merge_profile_events,
    merge_skill_events,
)
from backend.modules.memory.facade import EventSpec, get_memory
from backend.modules.memory.models import GrowthEvent
from backend.modules.profile.schemas import ProfilePayload

logger = get_logger(__name__)


async def get_profile_structured(user_id: str) -> dict:
    """查询 GrowthEvent 并合并为结构化画像数据。

    Returns:
        {profile, skills, experiences, goals, preferences, status, decisions}
    """
    async with get_async_session_maker()() as db:
        result = await db.execute(
            select(GrowthEvent).where(GrowthEvent.user_id == user_id).order_by(GrowthEvent.created_at.asc())
        )
        events = list(result.scalars().all())

    events_by_type: dict[str, list[GrowthEvent]] = {}
    for event in events:
        events_by_type.setdefault(event.event_type, []).append(event)

    profile = merge_profile_events(events_by_type.get("profile_updated", []))
    skills_dict = merge_skill_events(
        events_by_type.get("skill_added", []) + events_by_type.get("skill_level_changed", [])
    )
    experiences = merge_experience_events(events_by_type.get("experience_added", []))
    goals = merge_dict_events(events_by_type.get("goal_updated", []))
    preferences = merge_dict_events(events_by_type.get("preference_learned", []))
    status = merge_dict_events(events_by_type.get("status_changed", []))
    decisions = merge_decision_events(events_by_type.get("decision_made", []))

    return {
        "profile": profile,
        "skills": list(skills_dict.values()),
        "experiences": experiences,
        "goals": goals,
        "preferences": preferences,
        "status": status,
        "decisions": decisions,
    }


async def update_profile_structured(
    user_id: str,
    profile: dict | None,
    skills: list[dict] | None,
    experiences: list[dict] | None,
) -> int:
    """接受前端提交的画像更新，生成对应 GrowthEvent。

    Returns:
        创建的事件数量
    """
    events: list[EventSpec] = []

    if profile:
        allowed = set(ProfilePayload.model_fields.keys())
        clean_profile = {k: v for k, v in profile.items() if k in allowed and v is not None and v != ""}
        if "awards" in clean_profile and isinstance(clean_profile["awards"], str):
            try:
                clean_profile["awards"] = json.loads(clean_profile["awards"])
            except json.JSONDecodeError:
                clean_profile["awards"] = [x.strip() for x in clean_profile["awards"].split(",") if x.strip()]
        if clean_profile:
            events.append(
                {
                    "event_type": "profile_updated",
                    "entity_type": "profile",
                    "entity_id": "profile_fields",
                    "payload": clean_profile,
                    "source": "用户手动编辑",
                }
            )

    if skills:
        for skill in skills:
            if not isinstance(skill, dict) or not skill.get("name"):
                continue
            events.append(
                {
                    "event_type": "skill_added",
                    "entity_type": "skill",
                    "entity_id": str(uuid.uuid4()),
                    "payload": {
                        "name": skill["name"],
                        "level": skill.get("level", "familiar"),
                        "context": skill.get("context", ""),
                        "source": "用户手动编辑",
                    },
                    "source": "用户手动编辑",
                }
            )

    if experiences:
        for exp in experiences:
            if not isinstance(exp, dict) or not exp.get("title"):
                continue
            events.append(
                {
                    "event_type": "experience_added",
                    "entity_type": "experience",
                    "entity_id": str(uuid.uuid4()),
                    "payload": {
                        "title": exp["title"],
                        "description": exp.get("description", ""),
                        "period": exp.get("period", ""),
                        "tech_stack": exp.get("tech_stack", ""),
                        "role": exp.get("role", ""),
                        "source": "用户手动编辑",
                    },
                    "source": "用户手动编辑",
                }
            )

    if not events:
        return 0

    memory = get_memory()
    created = await memory.remember_batch(user_id, events)
    return len(created) if created else 0
