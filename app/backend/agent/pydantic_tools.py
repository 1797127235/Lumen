"""PydanticAI Agent 工具 — 使用 @agent.tool 装饰器

工具列表：
- get_profile: 读取用户画像（已废弃，改用 memory_search）
- update_profile: 从对话中增量更新画像（已废弃，改用 memory_update）
- memory_search: 搜索记忆内容
- memory_update: 更新记忆内容
- memory_add: 添加记忆内容
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent, RunContext

from app.backend.agent.deps import CareerOSDeps

logger = logging.getLogger(__name__)


def register_tools(agent: Agent[CareerOSDeps, str]) -> None:
    """注册所有工具到 Agent

    Args:
        agent: PydanticAI Agent 实例
    """

    # ── 记忆工具（新版）──────────────────────────────

    @agent.tool
    async def memory_search(
        ctx: RunContext[CareerOSDeps],
        query: str,
        entity_types: list[str] | None = None,
    ) -> str:
        """搜索记忆内容。当需要查找用户信息时调用。

        Args:
            query: 搜索关键词
            entity_types: 限定搜索的实体类型列表，如 ["skills", "experiences"]
                可选值：skills, experiences, preferences, goals, decisions, relationships, status
                如果不指定，搜索所有类型
        """
        logger.info("工具调用: memory_search, query=%s, entity_types=%s", query, entity_types)

        # 空查询检查
        if not query or not query.strip():
            return "请提供搜索关键词。"

        from app.backend.services.memory_service import read_entity, search_memory

        # 如果指定了实体类型，只搜索这些类型
        if entity_types:
            results = []
            for entity_type in entity_types:
                content = read_entity(entity_type)
                if content and query.lower() in content.lower():
                    results.append(
                        {
                            "file": f"entities/{entity_type}.md",
                            "section": entity_type,
                            "content": content[:500],  # 限制长度
                        }
                    )
            return str(results) if results else f"在 {entity_types} 中未找到相关内容。"

        # 否则搜索所有记忆
        results = search_memory(query)
        return str(results) if results else "未找到相关内容。"

    @agent.tool
    async def memory_update(
        ctx: RunContext[CareerOSDeps],
        entity_type: str,
        section: str,
        content: str,
    ) -> str:
        """更新记忆内容。当用户提到新信息时调用。

        Args:
            entity_type: 实体类型
                - memory: 核心记忆（学校、专业、年级、目标等）
                - skills: 技能
                - experiences: 经历
                - preferences: 偏好
                - goals: 目标
                - decisions: 决策
                - relationships: 关系
                - status: 当前状态
            section: 章节标题，如 "基础信息"、"编程语言"、"项目经历"
            content: 要更新的内容
        """
        logger.info("工具调用: memory_update, entity_type=%s, section=%s", entity_type, section)
        from app.backend.db.session import get_db
        from app.backend.services.md_projector import create_event_and_project_md

        # 映射 entity_type 到 event_type
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

        # 验证 entity_type
        if entity_type not in event_type_map:
            return f"未知的实体类型: {entity_type}。支持的类型: {', '.join(event_type_map.keys())}"

        event_type = event_type_map[entity_type]

        # 构建 payload
        payload = {
            "section": section,
            "content": content,
        }

        # 写入 growth_events + 同步投影 .md
        async for db in get_db():
            event = await create_event_and_project_md(
                db=db,
                user_id=ctx.deps.user_id,
                event_type=event_type,
                entity_type=entity_type,
                payload=payload,
                source="Agent工具",
            )
            if event:
                return f"已更新 {entity_type} 的 {section} 部分"
            else:
                return f"{entity_type} 的 {section} 部分已是最新，跳过更新"

        return "更新失败"

    @agent.tool
    async def memory_add(
        ctx: RunContext[CareerOSDeps],
        entity_type: str,
        section: str,
        content: str,
    ) -> str:
        """添加记忆内容。当用户提到新信息时调用。

        Args:
            entity_type: 实体类型
                - skills: 技能
                - experiences: 经历
                - preferences: 偏好
                - goals: 目标
                - decisions: 决策
                - relationships: 关系
                - status: 当前状态
            section: 章节标题，如 "编程语言"、"项目经历"
            content: 要添加的内容
        """
        logger.info("工具调用: memory_add, entity_type=%s, section=%s", entity_type, section)
        from app.backend.db.session import get_db
        from app.backend.services.md_projector import create_event_and_project_md

        # 映射 entity_type 到 event_type
        event_type_map = {
            "skills": "skill_added",
            "experiences": "experience_added",
            "preferences": "preference_learned",
            "goals": "goal_updated",
            "decisions": "decision_made",
            "relationships": "profile_updated",
            "status": "status_changed",
        }

        # 验证 entity_type
        if entity_type not in event_type_map:
            return f"未知的实体类型: {entity_type}。支持的类型: {', '.join(event_type_map.keys())}"

        event_type = event_type_map[entity_type]

        # 构建 payload
        payload = {
            "section": section,
            "content": content,
        }

        # 写入 growth_events + 同步投影 .md
        async for db in get_db():
            event = await create_event_and_project_md(
                db=db,
                user_id=ctx.deps.user_id,
                event_type=event_type,
                entity_type=entity_type,
                payload=payload,
                source="Agent工具",
            )
            if event:
                return f"已添加到 {entity_type} 的 {section} 部分"
            else:
                return f"{entity_type} 的 {section} 部分已存在，跳过添加"

        return "添加失败"

    # ── 旧版工具（已废弃，保留兼容性）─────────────────

    @agent.tool
    async def get_profile(ctx: RunContext[CareerOSDeps]) -> str:
        """读取用户画像。仅当用户明确要求「查看我的画像」「我的信息」时调用，不要主动调用。"""
        logger.info("工具调用: get_profile（已废弃，建议使用 memory_search）, user_id=%s", ctx.deps.user_id)
        from app.backend.services.memory_service import read_memory

        # 读取核心记忆
        memory_content = read_memory()
        if memory_content:
            return memory_content

        return "用户画像为空，请先上传简历或手动填写画像。"

    @agent.tool
    async def update_profile(
        ctx: RunContext[CareerOSDeps],
        fields: dict[str, Any],
    ) -> str:
        """更新用户画像。当用户提到以下信息时【必须】调用：
        - 学校、专业、年级（如"我是大三的"、"软件工程专业"）
        - 目标方向（如"想做AI Agent"、"后端开发"）
        - 目标公司（如"想去大厂"、"国企"）
        - 个人简介、城市、薪资期望等

        Args:
            fields: 要更新的字段字典，支持的字段：
                - school_name: 学校名称
                - major: 专业
                - grade: 年级（freshman/sophomore/junior/senior/graduate1/graduate2/graduate3）
                - target_direction: 目标方向（后端/前端/算法/AI等）
                - target_company_level: 目标公司（top/major/medium/state_owned）
                - bio: 个人简介
                - city: 城市
                - expected_salary: 期望薪资
                - english_level: 英语水平
        """
        logger.info(
            "工具调用: update_profile（已废弃，建议使用 memory_update）, user_id=%s, fields=%s",
            ctx.deps.user_id,
            fields,
        )
        from app.backend.services.memory_service import update_memory_section

        # 将字段转换为核心记忆格式
        parts = []
        if "school_name" in fields:
            parts.append(f"- 学校：{fields['school_name']}")
        if "major" in fields:
            parts.append(f"- 专业：{fields['major']}")
        if "grade" in fields:
            grade_map = {
                "freshman": "大一",
                "sophomore": "大二",
                "junior": "大三",
                "senior": "大四",
                "graduate1": "研一",
                "graduate2": "研二",
                "graduate3": "研三",
                "大一": "大一",
                "大二": "大二",
                "大三": "大三",
                "大四": "大四",
                "研一": "研一",
                "研二": "研二",
                "研三": "研三",
            }
            parts.append(f"- 年级：{grade_map.get(fields['grade'], fields['grade'])}")
        if "target_direction" in fields:
            parts.append(f"- 目标岗位：{fields['target_direction']}")
        if "target_company_level" in fields:
            level_map = {"top": "大厂", "major": "中厂", "medium": "小厂", "state_owned": "国企"}
            parts.append(
                f"- 目标公司类型：{level_map.get(fields['target_company_level'], fields['target_company_level'])}"
            )
        if "bio" in fields:
            parts.append(f"- 个人简介：{fields['bio']}")
        if "city" in fields:
            parts.append(f"- 意向城市：{fields['city']}")
        if "expected_salary" in fields:
            parts.append(f"- 期望薪资：{fields['expected_salary']}")
        if "english_level" in fields:
            parts.append(f"- 英语水平：{fields['english_level']}")

        if parts:
            update_memory_section("基础信息", "\n".join(parts))
            return f"画像已更新：{', '.join(fields.keys())}"
        else:
            return "没有需要更新的字段。"
