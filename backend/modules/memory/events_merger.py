"""事件合并与文本格式化 — markdown.py 与 snapshot.py 的共享纯函数层。"""

from __future__ import annotations

import json
import re
from datetime import datetime

from pydantic import ValidationError

from backend.modules.memory.models import GrowthEvent
from backend.modules.profile.schemas import (
    DecisionPayload,
    ExperiencePayload,
    KeyValuePayload,
    ProfilePayload,
    SkillPayload,
)


def deep_merge(base: dict, update: dict) -> dict:
    result = base.copy()
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_payload(event: GrowthEvent) -> dict:
    if not event.payload_json:
        return {}
    try:
        payload = json.loads(event.payload_json)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def extract_profile_fields(md_text: str) -> dict:
    patterns = {
        "school_name": r"- 学校：(.+)",
        "major": r"- 专业：(.+)",
        "grade": r"- 年级：(.+)",
        "graduation_year": r"- 毕业年份：(.+)",
        "school_level": r"- 学校层次：(.+)",
        "target_direction": r"- 目标岗位：(.+)",
        "target_company_level": r"- 目标公司类型：(.+)",
        "city": r"- 意向城市：(.+)",
        "gpa": r"- GPA：(.+)",
        "ranking": r"- 排名：(.+)",
        "english_level": r"## 英语水平\s*\n- (.+)",
        "expected_salary": r"## 期望薪资\s*\n- (.+)",
    }
    fields: dict = {}
    for key, pattern in patterns.items():
        m = re.search(pattern, md_text)
        if m:
            val = m.group(1).strip()
            if val and val != "（待填写）":
                fields[key] = val

    m = re.search(r"## 个人简介\s*\n(.+?)(?=\n##|\n---|\Z)", md_text, re.DOTALL)
    if m:
        val = m.group(1).strip()
        if val and val != "（待填写）":
            fields["bio"] = val

    return fields


def merge_profile_events(events: list[GrowthEvent]) -> dict:
    profile: dict = {}
    for event in events:
        payload = load_payload(event)
        if not payload:
            continue

        if isinstance(payload.get("memory_md"), str):
            profile = deep_merge(profile, extract_profile_fields(payload["memory_md"]))
            continue

        if payload.get("field") in {"resume", "memory_md"} and isinstance(payload.get("content"), str):
            profile = deep_merge(profile, extract_profile_fields(payload["content"]))
            continue

        try:
            validated = ProfilePayload.model_validate(payload).model_dump(exclude_none=True)
        except ValidationError:
            continue
        profile = deep_merge(profile, validated)

    return profile


def merge_skill_events(events: list[GrowthEvent]) -> dict[str, dict]:
    skills: dict[str, dict] = {}
    for event in events:
        payload = load_payload(event)
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


def merge_experience_events(events: list[GrowthEvent]) -> list[dict]:
    experiences: list[dict] = []
    seen_titles: set[str] = set()
    for event in events:
        payload = load_payload(event)
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


def merge_dict_events(events: list[GrowthEvent]) -> dict:
    result: dict = {}
    for event in events:
        payload = load_payload(event)
        if not payload:
            continue
        try:
            validated = KeyValuePayload.model_validate(payload)
        except ValidationError:
            continue
        result[validated.key] = validated.value
    return result


def merge_decision_events(events: list[GrowthEvent]) -> list[dict]:
    decisions: list[dict] = []
    for event in events:
        payload = load_payload(event)
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


def generate_memory_md(
    profile: dict,
    preferences: dict,
    status: dict,
    goals: dict,
    decisions: list[dict],
) -> str:
    date_str = datetime.now().strftime("%Y-%m-%d")
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
    parts.append(f"- 目标岗位：{profile.get('target_direction', '（待填写）')}")
    parts.append(f"- 目标公司类型：{profile.get('target_company_level', '（待填写）')}")
    parts.append(f"- 意向城市：{profile.get('city', '（待填写）')}")
    if goals:
        parts.append("- 已记录的目标：")
        for goal_name, goal_detail in goals.items():
            parts.append(f"  - **{goal_name}**：{goal_detail}")
    parts.append("")

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
    if decisions:
        parts.append("## 重要决策")
        for decision in decisions[-5:]:
            title = decision.get("title", "未命名决策")
            decision_text = decision.get("decision", "")
            parts.append(f"- **{title}**：{decision_text}")
        parts.append("")

    parts.append(f"---\n*最后更新：{date_str}*")
    return "\n".join(parts)


def _build_skills_section(skills: dict[str, dict]) -> str:
    """构建技能章节（合并到 memory.md）。"""
    if not skills:
        return ""
    lines = ["## 技能"]
    for skill_name, skill_info in skills.items():
        level = skill_info.get("level", "familiar")
        lines.append(f"- **{skill_name}**（{level}）")
        if skill_info.get("context"):
            lines.append(f"  - {skill_info['context'][:80]}")
    return "\n".join(lines)


def _build_experiences_section(experiences: list[dict]) -> str:
    """构建经历章节（合并到 memory.md），最多 5 条。"""
    if not experiences:
        return ""
    lines = ["## 经历"]
    for exp in experiences[:5]:
        title = exp.get("title") or exp.get("name") or exp.get("company") or "未命名经历"
        lines.append(f"- **{title}**")
        if exp.get("period"):
            lines.append(f"  - {exp['period']}")
        if exp.get("role"):
            lines.append(f"  - {exp['role']}")
        if exp.get("tech_stack"):
            lines.append(f"  - {exp['tech_stack']}")
        if exp.get("description"):
            lines.append(f"  - {exp['description'][:60]}")
    return "\n".join(lines)


__all__ = [
    "_build_experiences_section",
    "_build_skills_section",
    "deep_merge",
    "extract_profile_fields",
    "generate_memory_md",
    "load_payload",
    "merge_decision_events",
    "merge_dict_events",
    "merge_experience_events",
    "merge_profile_events",
    "merge_skill_events",
]
