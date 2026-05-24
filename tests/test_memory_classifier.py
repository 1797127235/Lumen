"""测试 AI 伙伴内存模型的事件分类器。"""

from __future__ import annotations

import pytest

from lib.memory.classifier import (
    NARRATIVE_EVENT_TYPES,
    PROFILE_EVENT_TYPES,
    classify,
    is_l0_fixed,
    is_profile,
)


# ── 新的 Profile 事件类型 ──────────────────────────────────────
def test_profile_types_are_correct():
    assert "profile_updated" in PROFILE_EVENT_TYPES
    assert "interest_observed" in PROFILE_EVENT_TYPES
    assert "value_surfaced" in PROFILE_EVENT_TYPES
    assert "preference_learned" in PROFILE_EVENT_TYPES
    assert "emotional_pattern" in PROFILE_EVENT_TYPES


def test_old_career_types_not_in_profile():
    """旧的职业维度类型不应出现在新模型中。"""
    assert "skill_added" not in PROFILE_EVENT_TYPES
    assert "skill_level_changed" not in PROFILE_EVENT_TYPES
    assert "goal_updated" not in PROFILE_EVENT_TYPES
    assert "status_changed" not in PROFILE_EVENT_TYPES


# ── 新的 Narrative 事件类型 ───────────────────────────────────
def test_narrative_types_are_correct():
    assert "significant_moment" in NARRATIVE_EVENT_TYPES
    assert "decision_made" in NARRATIVE_EVENT_TYPES
    assert "reflection_added" in NARRATIVE_EVENT_TYPES
    assert "contradiction_noted" in NARRATIVE_EVENT_TYPES
    assert "relationship_noted" in NARRATIVE_EVENT_TYPES


def test_old_narrative_types_not_in_new_model():
    assert "experience_added" not in NARRATIVE_EVENT_TYPES
    assert "note_added" not in NARRATIVE_EVENT_TYPES


# ── classify() ───────────────────────────────────────────────
@pytest.mark.parametrize(
    "event_type,expected",
    [
        ("significant_moment", "narrative"),
        ("decision_made", "narrative"),
        ("reflection_added", "narrative"),
        ("contradiction_noted", "narrative"),
        ("relationship_noted", "narrative"),
        ("profile_updated", "profile"),
        ("interest_observed", "profile"),
        ("value_surfaced", "profile"),
        ("preference_learned", "profile"),
        ("emotional_pattern", "profile"),
    ],
)
def test_classify_new_types(event_type, expected):
    assert classify(event_type) == expected


def test_classify_unknown_defaults_to_narrative():
    assert classify("some_unknown_type") == "narrative"


# ── L0 固定块 ─────────────────────────────────────────────────
def test_l0_fixed_types():
    assert is_l0_fixed("interest_observed")
    assert is_l0_fixed("value_surfaced")
    assert is_l0_fixed("preference_learned")
    assert is_l0_fixed("profile_updated")


def test_emotional_pattern_not_in_l0():
    """emotional_pattern 是 Profile 类型但不在 L0 固定块里。"""
    assert is_profile("emotional_pattern")
    assert not is_l0_fixed("emotional_pattern")


# ── is_profile() ──────────────────────────────────────────────
def test_is_profile():
    assert is_profile("profile_updated")
    assert not is_profile("significant_moment")
    assert not is_profile("decision_made")
