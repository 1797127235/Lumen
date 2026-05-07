"""画像工具 — get_profile + update_profile。

get_profile 读取完整记忆上下文（优先使用 dynamic_prompt 缓存）。
update_profile 写入结构化画像字段，走 ProfilePayload 校验。
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, RunContext

from app.backend.agent.deps import LumenDeps
from app.backend.logging_config import get_logger

logger = get_logger(__name__)


def register(agent: Agent[LumenDeps, str]) -> None:
    @agent.tool
    async def get_profile(ctx: RunContext[LumenDeps]) -> str:
        """获取用户完整画像（通常无需主动调用，画像已在 system prompt 中）。"""
        logger.info("Tool call: get_profile", user_id=ctx.deps.user_id)

        if ctx.deps.build_context_cache.strip():
            return ctx.deps.build_context_cache

        from app.backend.memory import get_memory

        memory = get_memory()
        context = await memory.build_context(ctx.deps.user_id)
        if context.strip():
            return context
        return "用户画像为空，请先上传简历或手动填写画像。"

    @agent.tool
    async def update_profile(
        ctx: RunContext[LumenDeps],
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

        from app.backend.memory import get_memory
        from app.backend.schemas.memory_events import ProfilePayload

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
