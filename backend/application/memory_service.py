"""画像 & 记忆应用服务层 — 业务逻辑"""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, Field
from pydantic_ai import Agent, PromptedOutput
from sqlalchemy import select

from backend.agent.pydantic_agent import _create_model
from backend.db import get_async_session_maker
from backend.domain.models import GrowthEvent
from backend.domain.schemas import ProfilePayload
from backend.logging_config import get_logger
from backend.memory.events_merger import (
    merge_decision_events,
    merge_dict_events,
    merge_experience_events,
    merge_profile_events,
    merge_skill_events,
)
from backend.memory.facade import EventSpec, get_memory
from backend.utils.parsers import parse_file

logger = get_logger(__name__)


# ── Resume upload Agent schemas ──


class _ResumeSkillItem(BaseModel):
    name: str = Field(min_length=1)
    level: str = Field(default="familiar", description="技能熟练度：familiar / proficient / expert")
    context: str = Field(default="")


class _ResumeExperienceItem(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(default="")
    period: str | None = Field(default=None)
    tech_stack: str | None = Field(default=None)
    role: str | None = Field(default=None)


class _ResumeProfile(BaseModel):
    school_name: str = Field(default="", description="学校全称，如'北京大学'")
    school_level: str = Field(default="", description="学校层次，如'985'、'211'、'一本'、'海外'等")
    major: str = Field(default="", description="专业名称")
    grade: str = Field(default="", description="年级，如'大三'、'研二'")
    graduation_year: str = Field(default="", description="预计毕业年份，如'2026'")
    gpa: str = Field(default="", description="GPA / 绩点，如'3.8/4.0'")
    ranking: str = Field(default="", description="专业排名，如'前10%'")
    english_level: str = Field(default="", description="英语水平，如'CET-6 580'、'雅思7.0'")
    city: str = Field(default="", description="意向城市，如'北京'、'上海'")
    target_direction: str = Field(default="", description="目标岗位方向，如'后端开发'、'产品经理'")
    target_company_level: str = Field(default="", description="目标公司层次，如'大厂'、'国企'、'外企'")
    expected_salary: str = Field(default="", description="期望薪资，如'25k-35k'")
    bio: str = Field(default="", description="个人简介/自我评价，一段话")
    awards: list[str] = Field(default_factory=list, description="获奖情况列表")


class _ResumeParseResult(BaseModel):
    profile: _ResumeProfile = Field(default_factory=_ResumeProfile)
    skills: list[_ResumeSkillItem] = Field(default_factory=list)
    experiences: list[_ResumeExperienceItem] = Field(default_factory=list)


_LEVEL_MAP = {
    "beginner": "familiar",
    "intermediate": "proficient",
    "advanced": "proficient",
    "expert": "expert",
    "proficient": "proficient",
    "familiar": "familiar",
}


# ── 简历解析 ──


async def upload_resume(content: bytes, filename: str, user_id: str) -> dict:
    """解析简历，提取信息，写入记忆层。

    Returns:
        {profile, skills, experiences, events_count}
    """
    # 1. 解析文件
    parsed = await parse_file(filename, content)
    text = parsed.text

    # 2. 截断到约 5000 字
    MAX_CHARS = 5000
    truncated = text[:MAX_CHARS] if len(text) > MAX_CHARS else text

    # 3. 调用 Agent 提取结构化信息
    model = _create_model()
    agent = Agent(
        model=model,
        output_type=PromptedOutput(
            _ResumeParseResult,
            name="resume_parse",
            description="从简历文本中提取结构化信息",
        ),
        system_prompt=(
            "你是一位简历解析专家。请从简历文本中提取结构化信息。\n"
            "要求：\n"
            "1. 尽可能提取准确信息，不确定的字段留空字符串。\n"
            "2. skills 数组中的 level 字段只能是：familiar（入门/了解）/ proficient（熟练/中级）/ expert（精通/高级/专家）。\n"
            "3. experiences 按时间倒序排列。\n"
            "4. 如果某项信息不存在，返回空数组或空字符串，不要编造。"
        ),
    )

    result = await agent.run(f"简历内容：\n{truncated}")
    parsed_result = result.output

    # 4. 后处理
    profile_raw = parsed_result.profile.model_dump() if parsed_result.profile else {}
    profile = {k: v for k, v in profile_raw.items() if v not in (None, "", [])}
    skills = [
        {
            "name": s.name,
            "level": _LEVEL_MAP.get(s.level.lower(), "familiar"),
            "context": s.context,
        }
        for s in (parsed_result.skills or [])
        if s.name
    ]
    experiences = [
        {
            "title": e.title,
            "description": e.description,
            "period": e.period or "",
            "tech_stack": e.tech_stack or "",
            "role": e.role or "",
        }
        for e in (parsed_result.experiences or [])
        if e.title
    ]

    # 5. 组装事件并写入
    events: list[EventSpec] = []
    if profile:
        events.append(
            {
                "event_type": "profile_updated",
                "entity_type": "user",
                "entity_id": user_id,
                "payload": profile,
                "source": "resume_upload",
            }
        )
    for skill in skills:
        events.append(
            {
                "event_type": "skill_added",
                "entity_type": "skill",
                "entity_id": str(uuid.uuid4()),
                "payload": skill,
                "source": "resume_upload",
            }
        )
    for exp in experiences:
        events.append(
            {
                "event_type": "experience_added",
                "entity_type": "experience",
                "entity_id": str(uuid.uuid4()),
                "payload": exp,
                "source": "resume_upload",
            }
        )

    memory = get_memory()
    if events:
        await memory.remember_batch(user_id, events)

    logger.info(
        "Resume uploaded and parsed",
        user_id=user_id,
        filename=filename,
        events=len(events),
        profile_fields=len(profile),
        skills=len(skills),
        experiences=len(experiences),
    )

    return {
        "profile": profile,
        "skills": skills,
        "experiences": experiences,
        "events_count": len(events),
    }


# ── 画像结构化读取 ──


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


# ── 画像批量更新 ──


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
