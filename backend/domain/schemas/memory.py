"""记忆事件类型常量 & Payload 映射"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from backend.domain.schemas.knowledge import FilePayload
from backend.domain.schemas.profile import (
    DecisionPayload,
    ExperiencePayload,
    KeyValuePayload,
    ProfilePayload,
    SkillPayload,
)

EventType = Literal[
    "profile_updated",
    "skill_added",
    "skill_level_changed",
    "experience_added",
    "preference_learned",
    "goal_updated",
    "status_changed",
    "decision_made",
]

ENTITY_TYPE_MAP: dict[str, str] = {
    "profile_updated": "profile",
    "skill_added": "skill",
    "skill_level_changed": "skill",
    "experience_added": "experience",
    "preference_learned": "preference",
    "goal_updated": "goal",
    "status_changed": "status",
    "decision_made": "decision",
}

EVENT_PAYLOAD_MAP: dict[str, type[BaseModel]] = {
    "profile_updated": ProfilePayload,
    "skill_added": SkillPayload,
    "skill_level_changed": SkillPayload,
    "experience_added": ExperiencePayload,
    "preference_learned": KeyValuePayload,
    "goal_updated": KeyValuePayload,
    "status_changed": KeyValuePayload,
    "decision_made": DecisionPayload,
    "document_uploaded": FilePayload,
}
