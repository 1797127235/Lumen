"""事件分类器 — 双管线路由的单一真相源。

Profile 管线：描述用户「是谁」— 身份、技能、目标、偏好、状态
             → .md 投影 + L0 固定注入，不进 FTS5/Cognee

Narrative 管线：描述用户「经历了什么」— 经历、决策、上传
               → FTS5/Cognee 索引 + L2 按需召回，也记录在 .md 展示区
"""

from __future__ import annotations

from typing import Literal

# ── Profile 事件：用户画像/状态，永远注入 L0，不需要搜索 ──
PROFILE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "profile_updated",
        "skill_added",
        "skill_level_changed",
        "goal_updated",
        "preference_learned",
        "status_changed",
    }
)

# ── Narrative 事件：用户时间线/行动，需要搜索召回 ──
NARRATIVE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "experience_added",
        "decision_made",
        "document_uploaded",
    }
)

# ── L0 固定块聚合使用的 Profile 子集（不包含 status_changed）──
L0_FIXED_BLOCK_TYPES: frozenset[str] = frozenset(
    {
        "profile_updated",
        "skill_added",
        "skill_level_changed",
        "goal_updated",
        "preference_learned",
    }
)

PipelineName = Literal["profile", "narrative"]


def classify(event_type: str) -> PipelineName:
    """返回事件类型所属管线。

    Raises:
        ValueError: 未知事件类型
    """
    if event_type in PROFILE_EVENT_TYPES:
        return "profile"
    if event_type in NARRATIVE_EVENT_TYPES:
        return "narrative"
    raise ValueError(f"Unknown event_type: {event_type!r}")


def is_indexable(event_type: str) -> bool:
    """该事件类型是否应被索引到 FTS5/Cognee（用于 L2 搜索召回）。"""
    return event_type in NARRATIVE_EVENT_TYPES


def is_l0_fixed(event_type: str) -> bool:
    """该事件类型是否参与 L0 固定块画像聚合。"""
    return event_type in L0_FIXED_BLOCK_TYPES


def is_profile(event_type: str) -> bool:
    """该事件类型是否属于 Profile 管线。"""
    return event_type in PROFILE_EVENT_TYPES


__all__ = [
    "L0_FIXED_BLOCK_TYPES",
    "NARRATIVE_EVENT_TYPES",
    "PROFILE_EVENT_TYPES",
    "PipelineName",
    "classify",
    "is_indexable",
    "is_l0_fixed",
    "is_profile",
]
