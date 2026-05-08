"""Agent 系统提示快照 — 分层注入（固定块 + 近期块 + 语义召回）。

三层记忆注入：
- L0 固定块：用户身份、目标、技能、偏好（800 字预算，动态分配）
- L1 近期块：最近 N 条事件（类型权重过滤）
- L2 语义召回：由 facade.build_context() 根据 user_input 追加（Cognee→FTS5→.md）

缓存策略：projection flush/sync/rebuild 时由 facade 触发 invalidate_cache() 失效；
           同时缓存有 5 分钟 TTL，确保 L1 时间窗口准实时。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.backend.db.base import get_async_session_maker
from app.backend.logging_config import get_logger
from app.backend.models.growth_event import GrowthEvent

logger = get_logger(__name__)

# ── 配置：固定块预算 ──
_FIXED_BUDGET = 800  # 字符上限
_FIXED_ALLOCATION = {
    "identity": 300,  # profile（学校/专业/年级/目标）
    "goals": 200,  # 当前目标
    "skills": 200,  # 技能列表
    "preferences": 100,  # 偏好（剩余预算）
}

# ── 配置：近期块 ──
_RECENT_LIMIT = 10  # 取最近 N 条事件
_RECENT_MAX_AGE_DAYS = 30  # 超过 30 天的事件不进入近期块

# ── 事件类型衰减权重（0.0=不衰减，1.0=最快衰减）──
# 衰减逻辑：事件年龄（天）* 权重 → 得分，低于阈值时过滤
_EVENT_DECAY_WEIGHTS: dict[str, float] = {
    "profile_updated": 0.0,  # 身份：永远注入（固定块处理）
    "goal_updated": 0.1,  # 目标：缓慢衰减
    "skill_added": 0.2,  # 技能：中等衰减
    "skill_level_changed": 0.2,  # 技能变更：中等衰减
    "preference_learned": 0.3,  # 偏好：中等偏快
    "status_changed": 0.4,  # 状态：快速衰减
    "experience_added": 0.3,  # 经历：中等偏快（原为0.5，10天丢弃太激进）
    "decision_made": 0.3,  # 决策：中等偏快
}

# 衰减阈值：得分 > 此值时丢弃
_DECAY_THRESHOLD = 5.0

# 缓存 TTL（分钟）
_CACHE_TTL_MINUTES = 5


@dataclass
class _CacheEntry:
    """带时间戳的缓存条目。"""

    user_id: str
    content: str
    created_at: datetime
    recent_event_ids: set[str]  # L1 中包含的事件 ID，用于 L2 去重


# 用户级缓存：user_id → _CacheEntry
_static_cache: dict[str, _CacheEntry] = {}


def invalidate_cache(user_id: str) -> None:
    """projection flush/sync/rebuild 时调用，使缓存失效。"""
    _static_cache.pop(user_id, None)


def get_recent_event_ids(user_id: str) -> set[str]:
    """获取当前缓存中 L1 近期块包含的事件 ID 集合（供 L2 去重使用）。

    如果缓存不存在或已过期，返回空集合。
    """
    entry = _static_cache.get(user_id)
    if entry is None:
        return set()
    if (datetime.now(UTC) - entry.created_at) >= timedelta(minutes=_CACHE_TTL_MINUTES):
        return set()
    return entry.recent_event_ids


def _truncate(text: str, limit: int, suffix: str = "…") -> str:
    """按字符截断文本，保留后缀。"""
    if len(text) <= limit:
        return text
    return text[: limit - len(suffix)] + suffix


def _build_fixed_block(
    profile: dict,
    goals: dict,
    skills: dict,
    preferences: dict,
) -> str:
    """构建 L0 固定块（身份 + 目标 + 技能 + 偏好），总预算 800 字。"""
    parts: list[str] = []
    budget = _FIXED_BUDGET
    # ── 身份（300 字）──
    identity_lines: list[str] = []
    identity_lines.append("## 身份")
    if profile.get("school_name"):
        identity_lines.append(f"- 学校：{profile['school_name']}")
    if profile.get("major"):
        identity_lines.append(f"- 专业：{profile['major']}")
    if profile.get("grade"):
        identity_lines.append(f"- 年级：{profile['grade']}")
    if profile.get("target_direction"):
        identity_lines.append(f"- 目标：{profile['target_direction']}")
    if profile.get("city"):
        identity_lines.append(f"- 城市：{profile['city']}")
    identity_text = "\n".join(identity_lines)
    identity_truncated = _truncate(identity_text, _FIXED_ALLOCATION["identity"])
    parts.append(identity_truncated)
    budget -= len(identity_truncated)

    # ── 目标（200 字）──
    if goals:
        goals_lines = ["## 目标"]
        for name, detail in list(goals.items())[:5]:
            goals_lines.append(f"- {name}：{str(detail)[:60]}")
        goals_text = "\n".join(goals_lines)
        goals_truncated = _truncate(goals_text, _FIXED_ALLOCATION["goals"])
        parts.append(goals_truncated)
        budget -= len(goals_truncated)

    # ── 技能（200 字）──
    if skills:
        skills_lines = ["## 技能"]
        for name, info in list(skills.items())[:8]:
            level = info.get("level", "")
            skills_lines.append(f"- {name}" + (f"（{level}）" if level else ""))
        skills_text = "\n".join(skills_lines)
        skills_truncated = _truncate(skills_text, _FIXED_ALLOCATION["skills"])
        parts.append(skills_truncated)
        budget -= len(skills_truncated)

    # ── 偏好（剩余预算）──
    if preferences and budget > 50:
        pref_lines = ["## 偏好"]
        for key, value in list(preferences.items())[:5]:
            pref_lines.append(f"- {key}：{str(value)[:40]}")
        pref_text = "\n".join(pref_lines)
        pref_truncated = _truncate(pref_text, max(budget, _FIXED_ALLOCATION["preferences"]))
        parts.append(pref_truncated)

    return "\n\n".join(parts)


def _build_recent_block(events: list) -> tuple[str, set[str]]:
    """构建 L1 近期块：最近事件，按类型衰减过滤。

    Returns:
        (block_text, event_ids): 文本块 + 包含的事件 ID 集合（用于 L2 去重）
    """
    now = datetime.now(UTC)
    filtered: list[tuple[str, str, float, str]] = []  # (event_type, content, score, event_id)

    for event in events:
        if not event.created_at:
            continue

        # 计算年龄（天）
        age_days = (now - event.created_at.replace(tzinfo=UTC)).days
        if age_days > _RECENT_MAX_AGE_DAYS:
            continue

        # profile_updated 已在固定块处理，不进入近期块
        if event.event_type == "profile_updated":
            continue

        # 获取衰减权重
        weight = _EVENT_DECAY_WEIGHTS.get(event.event_type, 0.3)

        # 计算衰减得分
        score = age_days * weight
        if score > _DECAY_THRESHOLD:
            continue

        # 提取内容
        content = ""
        if event.payload_json:
            try:
                payload = json.loads(event.payload_json)
                if isinstance(payload, dict):
                    content = payload.get("content") or payload.get("value") or payload.get("memory_md", "")
            except json.JSONDecodeError:
                pass
        if not content:
            content = f"{event.event_type}: {event.entity_type or ''}"

        filtered.append((event.event_type, content[:120], score, str(event.id)))

    if not filtered:
        return "", set()

    # 按得分排序（越新/越重要越靠前），取前 N 条
    filtered.sort(key=lambda x: x[2])
    top = filtered[:_RECENT_LIMIT]

    lines = ["## 近期动态"]
    event_ids: set[str] = set()
    for event_type, content, _score, eid in top:
        lines.append(f"- [{event_type}] {content}")
        event_ids.add(eid)

    return "\n".join(lines), event_ids


async def build_snapshot(user_id: str) -> str:
    """构建 Agent 系统提示快照（分层注入）。

    L0: 固定块（身份/目标/技能/偏好）— 800 字预算（读取全量事件）
    L1: 近期块（最近事件，类型衰减过滤）— 只读 30 天内
    L2: 语义召回 — 由 facade.build_context() 追加

    缓存 5 分钟 TTL，确保 L1 时间窗口准实时。
    """
    # 检查缓存（含 TTL）
    cached = _static_cache.get(user_id)
    if cached and (datetime.now(UTC) - cached.created_at) < timedelta(minutes=_CACHE_TTL_MINUTES):
        return cached.content

    from app.backend.memory.projections.events_merger import (
        merge_dict_events,
        merge_profile_events,
        merge_skill_events,
    )

    # 一次全量查询，Python 内部分离 L0（固定块）和 L1（近期块）
    cutoff = datetime.now(UTC) - timedelta(days=_RECENT_MAX_AGE_DAYS)
    async with get_async_session_maker()() as db:
        stmt = select(GrowthEvent).where(GrowthEvent.user_id == user_id).order_by(GrowthEvent.created_at.desc())
        result = await db.execute(stmt)
        all_events = list(result.scalars().all())

    recent_events = [e for e in all_events if e.created_at and e.created_at >= cutoff]

    # 无数据时返回空标记
    if not all_events:
        result = "【用户画像为空】"
        _static_cache[user_id] = _CacheEntry(
            user_id=user_id,
            content=result,
            created_at=datetime.now(UTC),
            recent_event_ids=set(),
        )
        return result

    # 按类型分组（全量事件，用于固定块）
    events_by_type: dict[str, list] = defaultdict(list)
    for event in all_events:
        events_by_type[event.event_type].append(event)

    # 固定块数据：只取每个类型最新的（避免累积）
    profile = merge_profile_events(events_by_type.get("profile_updated", []))
    goals = merge_dict_events(events_by_type.get("goal_updated", []))
    skills = merge_skill_events(events_by_type.get("skill_added", []) + events_by_type.get("skill_level_changed", []))
    preferences = merge_dict_events(events_by_type.get("preference_learned", []))

    # L0: 固定块
    fixed_block = _build_fixed_block(profile, goals, skills, preferences)

    # L1: 近期块（使用 30 天内事件）
    recent_block, recent_event_ids = _build_recent_block(recent_events)

    # 组装
    parts = [fixed_block]
    if recent_block:
        parts.append(recent_block)

    result = "\n\n".join(parts)
    _static_cache[user_id] = _CacheEntry(
        user_id=user_id,
        content=result,
        created_at=datetime.now(UTC),
        recent_event_ids=recent_event_ids,
    )
    return result
