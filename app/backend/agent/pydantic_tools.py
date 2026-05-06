"""Tool registration for the PydanticAI agent."""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent, RunContext

from app.backend.agent.deps import CareerOSDeps

logger = logging.getLogger(__name__)

# entity_type → SQLite event_type 映射
# 注意：memory 类型不走 memory_save，使用 update_profile 工具
_EVENT_TYPE_MAP: dict[str, str] = {
    "skills": "skill_added",
    "experiences": "experience_added",
    "preferences": "preference_learned",
    "goals": "goal_updated",
    "decisions": "decision_made",
    "status": "status_changed",
}


def register_tools(agent: Agent[CareerOSDeps, str]) -> None:
    @agent.tool
    async def memory_search(
        ctx: RunContext[CareerOSDeps],
        query: str,
        files: list[str] | None = None,
    ) -> str:
        """搜索记忆文件。files 可选 memory/skills/experiences，不传则全搜。"""
        logger.info("Tool call: memory_search, query=%s, files=%s", query, files)

        if not query or not query.strip():
            return "请提供搜索关键词。"

        from app.backend.services.memory_service import (
            read_experiences,
            read_memory,
            read_skills,
            search_memory,
        )

        uid = ctx.deps.user_id

        if files:
            readers = {"memory": read_memory, "skills": read_skills, "experiences": read_experiences}
            results = []
            for name in files:
                reader = readers.get(name)
                if not reader:
                    continue
                content = reader(uid)
                if content and query.lower() in content.lower():
                    results.append({"file": f"{name}.md", "section": name, "content": content[:500]})
            return str(results) if results else f"在 {files} 中未找到相关内容。"

        results = search_memory(uid, query)
        return str(results) if results else "未找到相关内容。"

    @agent.tool
    async def memory_save(
        ctx: RunContext[CareerOSDeps],
        entity_type: str,
        section: str,
        content: str,
    ) -> str:
        """保存或更新记忆条目。

        entity_type 说明：
        - skills       → 技能（掌握的技术、工具、语言）
        - experiences  → 经历（项目、实习、竞赛）
        - preferences  → 偏好（学习风格、工作偏好等）
        - goals        → 目标（短期/长期职业目标）
        - decisions    → 决策（重要的选择和结论）
        - status       → 当前状态（正在学习什么、焦虑什么）

        注意：memory 类型请使用 update_profile 工具（更新画像结构化字段）。
        """
        logger.info("Tool call: memory_save, entity_type=%s, section=%s", entity_type, section)

        if entity_type not in _EVENT_TYPE_MAP:
            return f"未知的类型 {entity_type}。支持: {', '.join(_EVENT_TYPE_MAP.keys())}"

        # 标记本轮已写入——后台提取器会跳过，避免双写
        ctx.deps.memory_tool_called = True

        from app.backend.schemas.memory_events import (
            DecisionPayload,
            ExperiencePayload,
            KeyValuePayload,
            SkillPayload,
        )
        from app.backend.services.md_projector import create_event_and_project_md

        # 按 entity_type 构建正确的 payload schema
        if entity_type == "skills":
            payload = SkillPayload(name=section, level="familiar", context=content, source="Agent工具").model_dump()
        elif entity_type == "experiences":
            payload = ExperiencePayload(title=section, description=content, source="Agent工具").model_dump()
        elif entity_type == "decisions":
            payload = DecisionPayload(title=section, content=content).model_dump()
        elif entity_type in ("preferences", "goals", "status"):
            payload = KeyValuePayload(key=section, value=content).model_dump()
        else:
            return f"不支持的类型 {entity_type}，请使用正确的工具"

        event = await create_event_and_project_md(
            db=ctx.deps.db,
            user_id=ctx.deps.user_id,
            event_type=_EVENT_TYPE_MAP[entity_type],
            entity_type=entity_type,
            entity_id=section,
            payload=payload,
            source="Agent工具",
        )
        if event:
            return f"已保存 {entity_type}/{section}"
        return f"{entity_type}/{section} 内容未变化，跳过"

    @agent.tool
    async def get_profile(ctx: RunContext[CareerOSDeps]) -> str:
        """获取用户完整画像（通常无需主动调用，画像已在 system prompt 中）。"""
        logger.info("Tool call: get_profile, user_id=%s", ctx.deps.user_id)
        from app.backend.services.md_projector import sync_user_md_projection
        from app.backend.services.memory_service import read_experiences, read_memory, read_skills

        memory_content = read_memory(ctx.deps.user_id)
        if not memory_content.strip():
            await sync_user_md_projection(ctx.deps.user_id)
            memory_content = read_memory(ctx.deps.user_id)

        parts = []
        if memory_content.strip():
            parts.append(memory_content)
        skills = read_skills(ctx.deps.user_id)
        if skills.strip():
            parts.append(skills)
        experiences = read_experiences(ctx.deps.user_id)
        if experiences.strip():
            parts.append(experiences)

        if parts:
            return "\n\n".join(parts)
        return "用户画像为空，请先上传简历或手动填写画像。"

    @agent.tool
    async def update_profile(
        ctx: RunContext[CareerOSDeps],
        fields: dict[str, Any],
    ) -> str:
        """批量更新画像结构化字段（如 school_name/major/grade 等）。"""
        logger.info("Tool call: update_profile, user_id=%s, fields=%s", ctx.deps.user_id, fields)
        if not fields:
            return "没有需要更新的字段。"

        # 标记本轮已写入
        ctx.deps.memory_tool_called = True

        from app.backend.schemas.memory_events import ProfilePayload
        from app.backend.services.md_projector import create_event_and_project_md

        # 校验：只保留 ProfilePayload 已知字段，丢弃未知 key；类型错则报错
        allowed_keys = set(ProfilePayload.model_fields.keys())
        known = {k: v for k, v in fields.items() if k in allowed_keys}
        discarded = [k for k in fields if k not in allowed_keys]
        if discarded:
            logger.warning("update_profile discarded unknown keys: %s", discarded)

        try:
            validated = ProfilePayload.model_validate(known)
        except Exception as e:
            return f"画像字段校验失败：{e}"

        event = await create_event_and_project_md(
            db=ctx.deps.db,
            user_id=ctx.deps.user_id,
            event_type="profile_updated",
            entity_type="profile",
            entity_id="profile_fields",
            payload=validated.model_dump(exclude_none=True),
            source="Agent工具",
        )
        if event:
            return f"画像已更新：{', '.join(validated.model_dump(exclude_none=True).keys())}"
        return "画像内容没有变化，跳过更新。"
