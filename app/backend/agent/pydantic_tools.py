"""Tool registration for the PydanticAI agent."""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, RunContext

from app.backend.agent.deps import CareerOSDeps
from app.backend.logging_config import get_logger

logger = get_logger(__name__)

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
        """搜索记忆。files 可选 memory/skills/experiences，不传则全搜。"""
        logger.info("Tool call: memory_search", query=query, files=files)

        if not query or not query.strip():
            return "请提供搜索关键词。"

        from app.backend.services.careeros_memory import get_memory

        memory = get_memory()
        uid = ctx.deps.user_id

        if files:
            from app.backend.services.memory_service import read_experiences, read_memory, read_skills

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

        items = await memory.recall(uid, query)
        if items:
            return "\n".join(
                f"- [{item.categories[0] if item.categories else '?'}] {item.content[:300]}" for item in items
            )
        return "未找到相关内容。"

    @agent.tool
    async def memory_save(
        ctx: RunContext[CareerOSDeps],
        entity_type: str,
        section: str,
        content: str,
    ) -> str:
        """保存记忆。主动调用！不要等用户要求！不要等用户说"请保存"或"帮我记住"！

        WHEN TO SAVE —— 遇到以下情况立即调用：
          用户说"我想做XX / 想学XX / 对XX感兴趣"
            → entity_type='goals', section=方向名, content=用户动机+上下文
          用户说"我会XX / 学过XX / 写过XX"
            → entity_type='skills', section=技能名, content=掌握程度
          用户分享经历（项目、实习、比赛）
            → entity_type='experiences', section=经历标题, content=描述
          用户纠正了你，或明确说"记住这个/别忘了"
            → entity_type='preferences', section=偏好名, content=说明
          用户做了重要选择或结论
            → entity_type='decisions', section=决策标题, content=理由
          用户表达了焦虑、困惑、当前状态
            → entity_type='status', section=状态描述, content=详情

        entity_type: skills/experiences/goals/preferences/decisions/status
        注意：结构化画像字段（学校、专业等）用 update_profile"""
        logger.info("Tool call: memory_save", entity_type=entity_type, section=section)

        if entity_type not in _EVENT_TYPE_MAP:
            return f"未知的类型 {entity_type}。支持: {', '.join(_EVENT_TYPE_MAP.keys())}"

        # Agent 工具主动写入，不依赖后台提取器

        from app.backend.schemas.memory_events import (
            DecisionPayload,
            ExperiencePayload,
            KeyValuePayload,
            SkillPayload,
        )
        from app.backend.services.careeros_memory import get_memory

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

        memory = get_memory()
        event = await memory.remember(
            user_id=ctx.deps.user_id,
            event_type=_EVENT_TYPE_MAP[entity_type],
            entity_type=entity_type,
            entity_id=section,
            payload=payload,
            source="Agent工具",
            db=ctx.deps.db,
        )
        if event and event.id is not None:
            ctx.deps.pending_event_ids.append(str(event.id))
            ctx.deps.build_context_cache = ""  # 写入后使缓存失效
            return f"已保存 {entity_type}/{section}"
        return f"{entity_type}/{section} 内容未变化，跳过"

    @agent.tool
    async def get_profile(ctx: RunContext[CareerOSDeps]) -> str:
        """获取用户完整画像（通常无需主动调用，画像已在 system prompt 中）。"""
        logger.info("Tool call: get_profile", user_id=ctx.deps.user_id)

        # Prefetch Lifecycle：优先使用 dynamic_prompt 缓存的结果
        if ctx.deps.build_context_cache.strip():
            return ctx.deps.build_context_cache

        from app.backend.services.careeros_memory import get_memory

        memory = get_memory()
        context = await memory.build_context(ctx.deps.user_id)
        if context.strip():
            return context
        return "用户画像为空，请先上传简历或手动填写画像。"

    @agent.tool
    async def update_profile(
        ctx: RunContext[CareerOSDeps],
        school_name: str | None = None,
        major: str | None = None,
        grade: str | None = None,
        graduation_year: str | None = None,
        school_level: str | None = None,
        target_direction: str | None = None,
        target_company_level: str | None = None,
        city: str | None = None,
        gpa: str | None = None,
        ranking: str | None = None,
        awards: list[str] | None = None,
        bio: str | None = None,
        english_level: str | None = None,
        expected_salary: str | None = None,
    ) -> str:
        """更新用户画像。只传有值的字段，传 None 的会被忽略。

        可用字段及含义：
        - school_name: 学校名称
        - major: 专业
        - grade: 年级（大一/大二/大三/大四/研一/研二/研三）
        - graduation_year: 毕业年份
        - school_level: 学校层次（985/211/双一流/普通本科/专科）
        - target_direction: 职业目标方向（如：后端开发/AI算法/前端开发/产品经理）
        - target_company_level: 目标公司层次（大厂/中厂/小厂/创业公司/无所谓）
        - city: 意向城市
        - gpa: GPA
        - ranking: 排名
        - awards: 获奖列表
        - bio: 个人简介
        - english_level: 英语水平（CET4/CET6/雅思/托福/无）
        - expected_salary: 期望薪资

        例：用户说「我在清华读大二，想做大厂AI算法」
          → update_profile(school_name='清华大学', grade='大二', target_direction='AI算法', target_company_level='大厂')"""

        # 收集非 None 字段
        fields: dict[str, Any] = {}
        for name, val in [
            ("school_name", school_name),
            ("major", major),
            ("grade", grade),
            ("graduation_year", graduation_year),
            ("school_level", school_level),
            ("target_direction", target_direction),
            ("target_company_level", target_company_level),
            ("city", city),
            ("gpa", gpa),
            ("ranking", ranking),
            ("awards", awards),
            ("bio", bio),
            ("english_level", english_level),
            ("expected_salary", expected_salary),
        ]:
            if val is not None:
                fields[name] = val

        if not fields:
            return "没有需要更新的字段。"

        # Agent 工具主动写入，不依赖后台提取器

        from app.backend.schemas.memory_events import ProfilePayload
        from app.backend.services.careeros_memory import get_memory

        # 校验：只保留 ProfilePayload 已知字段，丢弃未知 key；类型错则报错
        allowed_keys = set(ProfilePayload.model_fields.keys())
        known = {k: v for k, v in fields.items() if k in allowed_keys}
        discarded = [k for k in fields if k not in allowed_keys]
        if discarded:
            logger.warning("update_profile discarded unknown keys", discarded=discarded)

        try:
            validated = ProfilePayload.model_validate(known)
        except Exception as e:
            return f"画像字段校验失败：{e}"

        memory = get_memory()
        event = await memory.remember(
            user_id=ctx.deps.user_id,
            event_type="profile_updated",
            entity_type="profile",
            entity_id="profile_fields",
            payload=validated.model_dump(exclude_none=True),
            source="Agent工具",
            db=ctx.deps.db,
        )
        if event and event.id is not None:
            ctx.deps.pending_event_ids.append(str(event.id))
            ctx.deps.build_context_cache = ""  # 写入后使缓存失效
            return f"画像已更新：{', '.join(validated.model_dump(exclude_none=True).keys())}"
        return "画像内容没有变化，跳过更新。"
