"""Tool registration for the PydanticAI agent."""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent, RunContext

from app.backend.agent.deps import CareerOSDeps

logger = logging.getLogger(__name__)


def register_tools(agent: Agent[CareerOSDeps, str]) -> None:
    @agent.tool
    async def memory_search(
        ctx: RunContext[CareerOSDeps],
        query: str,
        entity_types: list[str] | None = None,
    ) -> str:
        logger.info("Tool call: memory_search, query=%s, entity_types=%s", query, entity_types)

        if not query or not query.strip():
            return "请提供搜索关键词。"

        from app.backend.services.memory_service import read_entity, search_memory

        if entity_types:
            results = []
            for entity_type in entity_types:
                content = read_entity(entity_type)
                if content and query.lower() in content.lower():
                    results.append(
                        {
                            "file": f"entities/{entity_type}.md",
                            "section": entity_type,
                            "content": content[:500],
                        }
                    )
            return str(results) if results else f"在 {entity_types} 中未找到相关内容。"

        results = search_memory(query)
        return str(results) if results else "未找到相关内容。"

    @agent.tool
    async def memory_update(
        ctx: RunContext[CareerOSDeps],
        entity_type: str,
        section: str,
        content: str,
    ) -> str:
        logger.info("Tool call: memory_update, entity_type=%s, section=%s", entity_type, section)
        from app.backend.db.session import get_db
        from app.backend.services.md_projector import create_event_and_project_md

        event_type_map = {
            "memory": "profile_updated",
            "skills": "skill_added",
            "experiences": "experience_added",
            "preferences": "preference_learned",
            "goals": "goal_updated",
            "decisions": "decision_made",
            "relationships": "profile_updated",
            "status": "status_changed",
        }
        if entity_type not in event_type_map:
            return f"未知的实体类型 {entity_type}。支持: {', '.join(event_type_map.keys())}"

        payload = {"section": section, "content": content}
        async for db in get_db():
            event = await create_event_and_project_md(
                db=db,
                user_id=ctx.deps.user_id,
                event_type=event_type_map[entity_type],
                entity_type=entity_type,
                entity_id=section,
                payload=payload,
                source="Agent工具",
            )
            if event:
                return f"已更新 {entity_type} 的 {section} 部分"
            return f"{entity_type} 的 {section} 部分已是最新，跳过更新"

        return "更新失败"

    @agent.tool
    async def memory_add(
        ctx: RunContext[CareerOSDeps],
        entity_type: str,
        section: str,
        content: str,
    ) -> str:
        logger.info("Tool call: memory_add, entity_type=%s, section=%s", entity_type, section)
        from app.backend.db.session import get_db
        from app.backend.services.md_projector import create_event_and_project_md

        event_type_map = {
            "skills": "skill_added",
            "experiences": "experience_added",
            "preferences": "preference_learned",
            "goals": "goal_updated",
            "decisions": "decision_made",
            "relationships": "profile_updated",
            "status": "status_changed",
        }
        if entity_type not in event_type_map:
            return f"未知的实体类型 {entity_type}。支持: {', '.join(event_type_map.keys())}"

        payload = {"section": section, "content": content}
        async for db in get_db():
            event = await create_event_and_project_md(
                db=db,
                user_id=ctx.deps.user_id,
                event_type=event_type_map[entity_type],
                entity_type=entity_type,
                entity_id=section,
                payload=payload,
                source="Agent工具",
            )
            if event:
                return f"已添加到 {entity_type} 的 {section} 部分"
            return f"{entity_type} 的 {section} 部分已存在，跳过添加"

        return "添加失败"

    @agent.tool
    async def get_profile(ctx: RunContext[CareerOSDeps]) -> str:
        logger.info("Tool call: get_profile, user_id=%s", ctx.deps.user_id)
        from app.backend.services.md_projector import sync_user_md_projection
        from app.backend.services.memory_service import read_memory

        memory_content = read_memory()
        if not memory_content.strip():
            projected = await sync_user_md_projection(ctx.deps.user_id)
            if projected:
                memory_content = read_memory()

        if memory_content:
            return memory_content
        return "用户画像为空，请先上传简历或手动填写画像。"

    @agent.tool
    async def update_profile(
        ctx: RunContext[CareerOSDeps],
        fields: dict[str, Any],
    ) -> str:
        logger.info("Tool call: update_profile, user_id=%s, fields=%s", ctx.deps.user_id, fields)
        if not fields:
            return "没有需要更新的字段。"

        from app.backend.db.session import get_db
        from app.backend.services.md_projector import create_event_and_project_md

        async for db in get_db():
            event = await create_event_and_project_md(
                db=db,
                user_id=ctx.deps.user_id,
                event_type="profile_updated",
                entity_type="profile",
                entity_id="profile_fields",
                payload=fields,
                source="Agent工具",
            )
            if event:
                return f"画像已更新：{', '.join(fields.keys())}"
            return "画像内容没有变化，跳过更新。"

        return "更新失败"
