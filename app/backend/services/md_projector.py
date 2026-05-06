"""将已提交的成长事件投影为 Markdown 快照。

简化为 3 个文件：
- memory.md: 核心画像 + 状态 + 目标 + 偏好 + 决策
- skills.md: 技能
- experiences.md: 经历
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import defaultdict
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.db.base import get_async_session_maker
from app.backend.models.growth_event import GrowthEvent
from app.backend.services.memory_limits import EXPERIENCES_CHAR_LIMIT, MEMORY_CHAR_LIMIT, SKILLS_CHAR_LIMIT
from app.backend.services.memory_service import ensure_memory_dirs, extract_profile_fields, memory_dir

logger = logging.getLogger(__name__)


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
    """合并 profile_updated 事件。新格式走 ProfilePayload schema，legacy memory_md blob 走正则提取。"""
    from app.backend.schemas.memory_events import ProfilePayload

    profile: dict = {}
    for event in events:
        payload = _load_payload(event)
        if not payload:
            continue

        # Legacy: payload 含 memory_md blob → 规则提取结构化字段
        if isinstance(payload.get("memory_md"), str):
            profile = _deep_merge(profile, extract_profile_fields(payload["memory_md"]))
            continue

        # Legacy: field/content 格式
        if payload.get("field") in {"resume", "memory_md"} and isinstance(payload.get("content"), str):
            profile = _deep_merge(profile, extract_profile_fields(payload["content"]))
            continue

        # New: schema 驱动
        try:
            validated = ProfilePayload.model_validate(payload).model_dump(exclude_none=True)
        except ValidationError:
            continue
        profile = _deep_merge(profile, validated)

    return profile


def _merge_skill_events(events: list[GrowthEvent]) -> dict[str, dict]:
    """合并技能事件，使用 SkillPayload schema。"""
    from app.backend.schemas.memory_events import SkillPayload

    skills: dict[str, dict] = {}
    for event in events:
        payload = _load_payload(event)
        if not payload:
            continue
        try:
            validated = SkillPayload.model_validate(payload)
        except ValidationError:
            continue
        skills[validated.name] = {
            "name": validated.name,
            "level": validated.level,
            "context": validated.context,
            "source": validated.source,
            "updated_at": event.created_at.isoformat() if event.created_at else None,
        }
    return skills


def _merge_experience_events(events: list[GrowthEvent]) -> list[dict]:
    """合并经历事件，使用 ExperiencePayload schema。"""
    from app.backend.schemas.memory_events import ExperiencePayload

    experiences: list[dict] = []
    seen_titles: set[str] = set()
    for event in events:
        payload = _load_payload(event)
        if not payload:
            continue
        try:
            validated = ExperiencePayload.model_validate(payload)
        except ValidationError:
            continue
        if validated.title in seen_titles:
            continue
        seen_titles.add(validated.title)
        experiences.append(
            {
                "title": validated.title,
                "description": validated.description,
                "period": validated.period,
                "tech_stack": validated.tech_stack,
                "role": validated.role,
                "source": validated.source,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
        )
    return experiences


def _merge_dict_events(events: list[GrowthEvent]) -> dict:
    """合并偏好/状态/目标事件，使用 KeyValuePayload schema。"""
    from app.backend.schemas.memory_events import KeyValuePayload

    result: dict = {}
    for event in events:
        payload = _load_payload(event)
        if not payload:
            continue
        try:
            validated = KeyValuePayload.model_validate(payload)
        except ValidationError:
            continue
        result[validated.key] = validated.value
    return result


def _merge_decision_events(events: list[GrowthEvent]) -> list[dict]:
    """合并决策事件，使用 DecisionPayload schema。"""
    from app.backend.schemas.memory_events import DecisionPayload

    decisions: list[dict] = []
    for event in events:
        payload = _load_payload(event)
        if not payload:
            continue
        try:
            validated = DecisionPayload.model_validate(payload)
        except ValidationError:
            continue
        decisions.append(
            {
                "title": validated.title,
                "decision": validated.content,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
        )
    return decisions


def _generate_memory_md(
    profile: dict,
    preferences: dict,
    status: dict,
    goals: dict,
    decisions: list[dict],
) -> str:
    """生成 memory.md：核心画像 + 状态 + 目标 + 偏好 + 决策。"""
    date = datetime.now().strftime("%Y-%m-%d")
    parts = ["# 用户核心记忆", ""]
    parts.append("> 这个文件由 AI 自动管理，记录用户的核心信息。")
    parts.append("> 每次对话开始时会自动注入到 system prompt。")
    parts.append("")

    # 基础信息
    parts.append("## 基础信息")
    parts.append(f"- 学校：{profile.get('school_name', '（待填写）')}")
    parts.append(f"- 专业：{profile.get('major', '（待填写）')}")
    parts.append(f"- 年级：{profile.get('grade', '（待填写）')}")
    parts.append(f"- 毕业年份：{profile.get('graduation_year', '（待填写）')}")
    if profile.get("school_level"):
        parts.append(f"- 学校层次：{profile['school_level']}")
    parts.append("")

    # 目标方向
    parts.append("## 目标方向")
    # 结构化字段优先（来自 update_profile）
    parts.append(f"- 目标岗位：{profile.get('target_direction', '（待填写）')}")
    parts.append(f"- 目标公司类型：{profile.get('target_company_level', '（待填写）')}")
    parts.append(f"- 意向城市：{profile.get('city', '（待填写）')}")
    # 自由形式目标（来自 memory_save('goals', ...)）
    if goals:
        parts.append("- 已记录的目标：")
        for goal_name, goal_detail in goals.items():
            parts.append(f"  - **{goal_name}**：{goal_detail}")
    parts.append("")

    # 教育背景
    if profile.get("gpa") or profile.get("ranking") or profile.get("awards") is not None:
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

    # 当前状态
    parts.append("## 当前状态")
    if status:
        for key, value in status.items():
            parts.append(f"- {key}：{value}")
    else:
        parts.append("- 正在学习：（待填写）")
        parts.append("- 正在准备：（待填写）")
        parts.append("- 焦虑程度：（待填写）")
    parts.append("")

    # 个人简介
    if profile.get("bio"):
        parts.append("## 个人简介")
        parts.append(str(profile["bio"]))
        parts.append("")

    # 英语水平
    if profile.get("english_level"):
        parts.append("## 英语水平")
        parts.append(f"- {profile['english_level']}")
        parts.append("")

    # 期望薪资
    if profile.get("expected_salary"):
        parts.append("## 期望薪资")
        parts.append(f"- {profile['expected_salary']}")
        parts.append("")

    # 关键偏好
    if preferences:
        parts.append("## 关键偏好")
        for key, value in preferences.items():
            parts.append(f"- {key}：{value}")
        parts.append("")

    # 重要决策
    if decisions:
        parts.append("## 重要决策")
        for decision in decisions[-5:]:  # 只保留最近 5 条
            title = decision.get("title", "未命名决策")
            decision_text = decision.get("decision", "")
            parts.append(f"- **{title}**：{decision_text}")
        parts.append("")

    parts.append(f"---\n*最后更新：{date}*")
    return "\n".join(parts)


def _generate_skills_md(skills: dict[str, dict]) -> str:
    """生成 skills.md：技能列表。"""
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
    """生成 experiences.md：经历列表。"""
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


def _truncate_to_limit(content: str, limit: int, *, keep_tail: bool = False) -> str:
    """截断内容到字符限制。
    keep_tail=False: 保留头部（memory.md 结构化字段在前）。
    keep_tail=True: 保留尾部（skills/experiences 新条目在末尾）。
    """
    if len(content) <= limit:
        return content
    if keep_tail:
        truncated = content[-limit:]
        # 找到第一个完整段落开始
        first_newline = truncated.find("\n\n")
        if first_newline >= 0:
            truncated = truncated[first_newline + 2 :]
    else:
        truncated = content[:limit]
        last_newline = truncated.rfind("\n\n")
        if last_newline > 0:
            truncated = truncated[:last_newline]
    logger.warning("Content truncated: %d -> %d chars (keep_tail=%s)", len(content), len(truncated), keep_tail)
    return truncated


def _write_md_file_safe(path: str, content: str, max_chars: int | None = None, *, keep_tail: bool = False) -> None:
    if max_chars is not None:
        content = _truncate_to_limit(content, max_chars, keep_tail=keep_tail)
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


def _write_default_md_snapshot(user_id: str) -> None:
    from app.backend.services.memory_templates import experiences_default, memory_default, skills_default

    ensure_memory_dirs(user_id)
    d = memory_dir(user_id)
    _write_md_file_safe(str(d / "memory.md"), memory_default())
    _write_md_file_safe(str(d / "skills.md"), skills_default())
    _write_md_file_safe(str(d / "experiences.md"), experiences_default())


async def project_user_to_md(db: AsyncSession, user_id: str) -> bool:
    try:
        result = await db.execute(
            select(GrowthEvent).where(GrowthEvent.user_id == user_id).order_by(GrowthEvent.created_at.asc())
        )
        events = list(result.scalars().all())

        if not events:
            _write_default_md_snapshot(user_id)
            logger.debug("No events found; rebuilt default markdown: user_id=%s", user_id)
            return True

        events_by_type: dict[str, list[GrowthEvent]] = defaultdict(list)
        for event in events:
            events_by_type[event.event_type].append(event)

        # 合并事件
        profile = _merge_profile_events(events_by_type.get("profile_updated", []))
        skills = _merge_skill_events(
            events_by_type.get("skill_added", []) + events_by_type.get("skill_level_changed", [])
        )
        experiences = _merge_experience_events(events_by_type.get("experience_added", []))
        preferences = _merge_dict_events(events_by_type.get("preference_learned", []))
        status = _merge_dict_events(events_by_type.get("status_changed", []))
        goals = _merge_dict_events(events_by_type.get("goal_updated", []))
        decisions = _merge_decision_events(events_by_type.get("decision_made", []))

        # 写入 3 个文件
        ensure_memory_dirs(user_id)
        d = memory_dir(user_id)

        _write_md_file_safe(
            str(d / "memory.md"),
            _generate_memory_md(profile, preferences, status, goals, decisions),
            max_chars=MEMORY_CHAR_LIMIT,
        )
        _write_md_file_safe(
            str(d / "skills.md"),
            _generate_skills_md(skills),
            max_chars=SKILLS_CHAR_LIMIT,
            keep_tail=True,
        )
        _write_md_file_safe(
            str(d / "experiences.md"),
            _generate_experiences_md(experiences),
            max_chars=EXPERIENCES_CHAR_LIMIT,
            keep_tail=True,
        )

        # 清理旧的 entities 目录（如果存在）
        entities_dir = d / "entities"
        if entities_dir.exists():
            import shutil

            shutil.rmtree(entities_dir, ignore_errors=True)
            logger.info("Removed legacy entities directory")

        now = datetime.now(datetime.UTC)
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


async def sync_user_md_projection(user_id: str) -> bool:
    async with get_async_session_maker()() as db:
        # dirty check：没有未投影事件则跳过全量重建
        from sqlalchemy import func, select

        dirty = await db.execute(
            select(func.count(GrowthEvent.id)).where(
                GrowthEvent.user_id == user_id,
                GrowthEvent.projected_md_at.is_(None),
            )
        )
        if (dirty.scalar() or 0) == 0:
            return True  # 无需投影

        success = await project_user_to_md(db, user_id)
        if success:
            await db.commit()
        else:
            await db.rollback()
        return success
