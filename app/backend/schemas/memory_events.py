"""记忆事件 Payload 类型定义。

所有 GrowthEvent 的 payload 必须匹配对应类型。
投影器按 schema 合并，不再猜测 key 名。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── profile_updated ──


class ProfilePayload(BaseModel):
    """画像更新。字段与 _generate_memory_md 的 consumer 对齐。"""

    # 基础信息
    school_name: str | None = None
    major: str | None = None
    grade: str | None = None
    graduation_year: str | None = None
    school_level: str | None = None

    # 目标方向
    target_direction: str | None = None
    target_company_level: str | None = None
    city: str | None = None

    # 教育背景
    gpa: str | None = None
    ranking: str | None = None
    awards: list[str] | None = None

    # 其他
    bio: str | None = None
    english_level: str | None = None
    expected_salary: str | None = None


# ── skill_added / skill_level_changed ──


class SkillPayload(BaseModel):
    name: str
    level: Literal["familiar", "proficient", "expert"] = "familiar"
    context: str = ""
    source: str = ""


# ── experience_added ──


class ExperiencePayload(BaseModel):
    title: str
    description: str = ""
    period: str = ""
    tech_stack: str = ""
    role: str = ""
    source: str = ""


# ── preference_learned / goal_updated / status_changed ──


class KeyValuePayload(BaseModel):
    key: str
    value: str


# ── decision_made ──


class DecisionPayload(BaseModel):
    title: str
    content: str


# ── file_ingested（未来扩展）──


class FilePayload(BaseModel):
    filename: str
    file_type: Literal["resume", "project", "notes", "generic"]
    file_hash: str = ""
    size_bytes: int = 0
    metadata: dict = Field(default_factory=dict)


# ── event_type → payload schema 映射（提取器校验用）──

EVENT_PAYLOAD_MAP: dict[str, type[BaseModel]] = {
    "profile_updated": ProfilePayload,
    "skill_added": SkillPayload,
    "skill_level_changed": SkillPayload,
    "experience_added": ExperiencePayload,
    "preference_learned": KeyValuePayload,
    "goal_updated": KeyValuePayload,
    "status_changed": KeyValuePayload,
    "decision_made": DecisionPayload,
}
