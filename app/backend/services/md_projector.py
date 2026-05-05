"""将已提交的成长事件投影为 Markdown 快照。"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.config import USER_DATA_DIR
from app.backend.db.base import get_async_session_maker
from app.backend.models.growth_event import GrowthEvent
from app.backend.services.memory_service import (
    _default_entity_template,
    _default_memory_template,
    ensure_memory_dirs,
)

logger = logging.getLogger(__name__)

# 字符限制（非 token），控制 system prompt 长度
MEMORY_CHAR_LIMIT = 3000  # memory.md
ENTITY_CHAR_LIMIT = 2000  # 每个实体文件


def _deep_merge(base: dict, update: dict) -> dict:
    result = base.copy()
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_payload(event: GrowthEvent) -> dict:
    if not event.payload_json:
        return {}
    try:
        payload = json.loads(event.payload_json)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _merge_profile_events(events: list[GrowthEvent]) -> dict:
    profile: dict = {}
    latest_memory_md: str | None = None

    for event in events:
        payload = _load_payload(event)
        if not payload:
            continue

        if isinstance(payload.get("memory_md"), str):
            latest_memory_md = payload["memory_md"]

        legacy_snapshot = (
            payload.get("content")
            if payload.get("field") in {"resume", "memory_md"} and isinstance(payload.get("content"), str)
            else None
        )
        if legacy_snapshot:
            latest_memory_md = legacy_snapshot

        structured_payload = payload.copy()
        structured_payload.pop("memory_md", None)
        if structured_payload.get("field") and "content" in structured_payload and latest_memory_md is None:
            structured_payload = {structured_payload["field"]: structured_payload["content"]}

        profile = _deep_merge(profile, structured_payload)

    if latest_memory_md:
        profile["__memory_md_snapshot"] = latest_memory_md
    return profile


def _merge_skill_events(events: list[GrowthEvent]) -> dict[str, dict]:
    skills: dict[str, dict] = {}
    for event in events:
        payload = _load_payload(event)
        if not payload:
            continue

        skill_name = (
            payload.get("name")
            or payload.get("skill")
            or payload.get("skill_name")
            or payload.get("section")
            or event.entity_id
        )
        if not skill_name:
            continue

        skills[skill_name] = {
            "name": skill_name,
            "level": payload.get("new_level") or payload.get("level", "familiar"),
            "context": payload.get("context") or payload.get("content", ""),
            "updated_at": event.created_at.isoformat() if event.created_at else None,
        }
    return skills


def _merge_experience_events(events: list[GrowthEvent]) -> list[dict]:
    experiences: list[dict] = []
    for event in events:
        payload = _load_payload(event)
        if not payload:
            continue
        if payload.get("section") and payload.get("content"):
            experiences.append(
                {
                    "title": payload["section"],
                    "description": payload["content"],
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
            )
        else:
            experiences.append(
                {
                    **payload,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
            )
    return experiences


def _merge_preference_events(events: list[GrowthEvent]) -> dict:
    preferences: dict = {}
    for event in events:
        payload = _load_payload(event)
        if not payload:
            continue
        if payload.get("section") and "content" in payload:
            preferences[payload["section"]] = payload["content"]
        else:
            preferences.update(payload)
    return preferences


def _merge_decision_events(events: list[GrowthEvent]) -> list[dict]:
    decisions: list[dict] = []
    for event in events:
        payload = _load_payload(event)
        if not payload:
            continue
        if payload.get("section") and payload.get("content"):
            decisions.append(
                {
                    "title": payload["section"],
                    "decision": payload["content"],
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
            )
        else:
            decisions.append(
                {
                    **payload,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
            )
    return decisions


def _merge_status_events(events: list[GrowthEvent]) -> dict:
    status: dict = {}
    for event in events:
        payload = _load_payload(event)
        if not payload:
            continue
        if payload.get("section") and "content" in payload:
            status[payload["section"]] = payload["content"]
        else:
            status.update(payload)
    return status


def _merge_goal_events(events: list[GrowthEvent]) -> dict:
    goals: dict = {}
    for event in events:
        payload = _load_payload(event)
        if not payload:
            continue
        if payload.get("section") and "content" in payload:
            goals[payload["section"]] = payload["content"]
        else:
            goals.update(payload)
    return goals


def _generate_memory_md(profile: dict, preferences: dict, status: dict, goals: dict) -> str:
    snapshot = profile.get("__memory_md_snapshot")
    if isinstance(snapshot, str) and snapshot.strip():
        return snapshot

    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 用户核心记忆", ""]
    parts.append("> 这个文件由 AI 自动管理，记录用户的核心信息。")
    parts.append("> 每次对话开始时会自动注入到 system prompt。")
    parts.append("")
    parts.append("## 基础信息")
    parts.append(f"- 学校：{profile.get('school_name', '（待填写）')}")
    parts.append(f"- 专业：{profile.get('major', '（待填写）')}")
    parts.append(f"- 年级：{profile.get('grade', '（待填写）')}")
    parts.append(f"- 毕业年份：{profile.get('graduation_year', '（待填写）')}")
    if profile.get("school_level"):
        parts.append(f"- 学校层次：{profile['school_level']}")
    parts.append("")
    parts.append("## 目标方向")
    parts.append(f"- 目标岗位：{profile.get('target_direction', goals.get('target_direction', '（待填写）'))}")
    parts.append(
        f"- 目标公司类型：{profile.get('target_company_level', goals.get('target_company_level', '（待填写）'))}"
    )
    parts.append(f"- 意向城市：{profile.get('city', goals.get('city', '（待填写）'))}")
    parts.append("")

    if profile.get("gpa") or profile.get("ranking") or profile.get("awards"):
        parts.append("## 教育背景")
        if profile.get("gpa"):
            parts.append(f"- GPA：{profile['gpa']}")
        if profile.get("ranking"):
            parts.append(f"- 排名：{profile['ranking']}")
        if profile.get("awards"):
            parts.append("- 获奖：")
            for award in profile["awards"]:
                parts.append(f"  - {award}")
        parts.append("")

    parts.append("## 当前状态")
    if status:
        for key, value in status.items():
            parts.append(f"- {key}：{value}")
    else:
        parts.append("- 正在学习：（待填写）")
        parts.append("- 正在准备：（待填写）")
        parts.append("- 焦虑程度：（待填写）")
    parts.append("")

    if profile.get("bio"):
        parts.append("## 个人简介")
        parts.append(str(profile["bio"]))
        parts.append("")

    if profile.get("english_level"):
        parts.append("## 英语水平")
        parts.append(f"- {profile['english_level']}")
        parts.append("")

    if profile.get("expected_salary"):
        parts.append("## 期望薪资")
        parts.append(f"- {profile['expected_salary']}")
        parts.append("")

    if preferences:
        parts.append("## 关键偏好")
        for key, value in preferences.items():
            parts.append(f"- {key}：{value}")
        parts.append("")

    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


def _generate_skills_md(skills: dict[str, dict]) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 技能列表", ""]
    parts.append("> 记录用户的技能状态，用于能力评估和学习建议。")
    parts.append("")
    if skills:
        parts.append("## 已掌握技能")
        for skill_name, skill_info in skills.items():
            parts.append(f"### {skill_name}")
            parts.append(f"- 状态：{skill_info.get('level', 'familiar')}")
            if skill_info.get("context"):
                parts.append(f"- 备注：{skill_info['context']}")
            parts.append("")
    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


def _generate_experiences_md(experiences: list[dict]) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 经历列表", ""]
    parts.append("> 记录用户的项目、实习、竞赛和其它成长经历。")
    parts.append("")
    for exp in experiences:
        title = exp.get("title") or exp.get("name") or exp.get("company") or "未命名经历"
        parts.append(f"### {title}")
        if exp.get("period"):
            parts.append(f"- 时间：{exp['period']}")
        elif exp.get("time"):
            parts.append(f"- 时间：{exp['time']}")
        if exp.get("role"):
            parts.append(f"- 角色：{exp['role']}")
        if exp.get("tech_stack"):
            parts.append(f"- 技术栈：{exp['tech_stack']}")
        if exp.get("description"):
            parts.append(f"- 描述：{exp['description']}")
        parts.append("")
    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


def _generate_preferences_md(preferences: dict) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 偏好列表", ""]
    parts.append("> 记录用户的偏好和习惯，用于个性化建议。")
    parts.append("")
    for key, value in preferences.items():
        parts.append(f"## {key}")
        parts.append(f"- {value}")
        parts.append("")
    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


def _generate_goals_md(goals: dict) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 目标列表", ""]
    parts.append("> 记录用户的短期和长期目标。")
    parts.append("")
    for key, value in goals.items():
        parts.append(f"## {key}")
        parts.append(f"- {value}")
        parts.append("")
    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


def _generate_decisions_md(decisions: list[dict]) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 决策记录", ""]
    parts.append("> 记录用户做出的重要决策。")
    parts.append("")
    for decision in decisions:
        parts.append(f"### {decision.get('title', '未命名决策')}")
        if decision.get("created_at"):
            parts.append(f"- 时间：{decision['created_at']}")
        if decision.get("background"):
            parts.append(f"- 背景：{decision['background']}")
        if decision.get("decision"):
            parts.append(f"- 决策：{decision['decision']}")
        if decision.get("reason"):
            parts.append(f"- 理由：{decision['reason']}")
        parts.append("")
    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


def _truncate_to_limit(content: str, limit: int) -> str:
    """截断内容到字符限制，保留最新的内容。"""
    if len(content) <= limit:
        return content
    # 保留尾部（最新内容在后面）
    truncated = content[-limit:]
    # 找到第一个完整段落开始
    first_newline = truncated.find("\n\n")
    if first_newline > 0:
        truncated = truncated[first_newline + 2 :]
    logger.warning("Content truncated: %d -> %d chars", len(content), len(truncated))
    return truncated


def _write_md_file_safe(path: str, content: str, max_chars: int | None = None) -> None:
    if max_chars is not None:
        content = _truncate_to_limit(content, max_chars)
    dir_name = os.path.dirname(path)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=dir_name,
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        temp_path = handle.name
    os.replace(temp_path, path)


def _write_default_md_snapshot() -> None:
    ensure_memory_dirs()
    memory_dir = USER_DATA_DIR / "memory"
    entities_dir = memory_dir / "entities"
    _write_md_file_safe(str(memory_dir / "memory.md"), _default_memory_template())
    for entity_type in [
        "skills",
        "experiences",
        "preferences",
        "goals",
        "decisions",
        "relationships",
        "status",
    ]:
        _write_md_file_safe(
            str(entities_dir / f"{entity_type}.md"),
            _default_entity_template(entity_type),
        )


async def project_user_to_md(db: AsyncSession, user_id: str) -> bool:
    try:
        result = await db.execute(
            select(GrowthEvent).where(GrowthEvent.user_id == user_id).order_by(GrowthEvent.created_at.asc())
        )
        events = list(result.scalars().all())

        if not events:
            _write_default_md_snapshot()
            logger.debug("No events found; rebuilt default markdown: user_id=%s", user_id)
            return True

        events_by_type: dict[str, list[GrowthEvent]] = defaultdict(list)
        for event in events:
            events_by_type[event.event_type].append(event)

        profile = _merge_profile_events(events_by_type.get("profile_updated", []))
        skills = _merge_skill_events(
            events_by_type.get("skill_added", []) + events_by_type.get("skill_level_changed", [])
        )
        experiences = _merge_experience_events(events_by_type.get("experience_added", []))
        preferences = _merge_preference_events(events_by_type.get("preference_learned", []))
        decisions = _merge_decision_events(events_by_type.get("decision_made", []))
        status = _merge_status_events(events_by_type.get("status_changed", []))
        goals = _merge_goal_events(events_by_type.get("goal_updated", []))

        ensure_memory_dirs()
        memory_dir = USER_DATA_DIR / "memory"
        entities_dir = memory_dir / "entities"

        _write_md_file_safe(
            str(memory_dir / "memory.md"),
            _generate_memory_md(profile, preferences, status, goals),
            max_chars=MEMORY_CHAR_LIMIT,
        )
        _write_md_file_safe(str(entities_dir / "skills.md"), _generate_skills_md(skills), max_chars=ENTITY_CHAR_LIMIT)
        _write_md_file_safe(
            str(entities_dir / "experiences.md"), _generate_experiences_md(experiences), max_chars=ENTITY_CHAR_LIMIT
        )
        _write_md_file_safe(
            str(entities_dir / "preferences.md"), _generate_preferences_md(preferences), max_chars=ENTITY_CHAR_LIMIT
        )
        _write_md_file_safe(str(entities_dir / "goals.md"), _generate_goals_md(goals), max_chars=ENTITY_CHAR_LIMIT)
        _write_md_file_safe(
            str(entities_dir / "decisions.md"), _generate_decisions_md(decisions), max_chars=ENTITY_CHAR_LIMIT
        )
        _write_md_file_safe(
            str(entities_dir / "relationships.md"),
            _default_entity_template("relationships"),
            max_chars=ENTITY_CHAR_LIMIT,
        )
        _write_md_file_safe(
            str(entities_dir / "status.md"), _default_entity_template("status"), max_chars=ENTITY_CHAR_LIMIT
        )

        now = datetime.utcnow()
        for event in events:
            event.projected_md_at = now
        await db.flush()

        logger.info(
            ".md projection complete: user_id=%s, events=%d, skills=%d, experiences=%d",
            user_id,
            len(events),
            len(skills),
            len(experiences),
        )
        return True
    except Exception as exc:
        logger.error(".md projection failed: user_id=%s, error=%s", user_id, exc)
        return False


async def project_incremental_md(db: AsyncSession, user_id: str) -> bool:
    return await project_user_to_md(db, user_id)


async def sync_user_md_projection(user_id: str) -> bool:
    async with get_async_session_maker()() as db:
        success = await project_user_to_md(db, user_id)
        if success:
            await db.commit()
        else:
            await db.rollback()
        return success


async def create_event_and_project_md(
    db: AsyncSession,
    user_id: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict | None = None,
    source: str = "system",
) -> GrowthEvent | None:
    from app.backend.services.growth_event_service import create_growth_event_with_dedup

    event = await create_growth_event_with_dedup(
        db=db,
        user_id=user_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        source=source,
    )
    if event is None:
        return None

    await db.commit()
    success = await sync_user_md_projection(user_id)
    if not success:
        raise RuntimeError(f".md projection failed after event commit: user_id={user_id}")
    return event
