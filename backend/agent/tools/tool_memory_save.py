"""memory_save 工具 — Agent 主动保存记忆。"""

from __future__ import annotations

from pydantic_ai import Agent, RunContext

from backend.agent.deps import LumenDeps
from backend.domain.schemas import DecisionPayload, ExperiencePayload, KeyValuePayload, SkillPayload
from backend.logging_config import get_logger
from backend.memory import get_memory

logger = get_logger(__name__)

_EVENT_TYPE_MAP: dict[str, str] = {
    "skills": "skill_added",
    "experiences": "experience_added",
    "preferences": "preference_learned",
    "goals": "goal_updated",
    "decisions": "decision_made",
    "status": "status_changed",
}


def register(agent: Agent[LumenDeps, str]) -> None:
    @agent.tool
    async def memory_save(
        ctx: RunContext[LumenDeps],
        entity_type: str,
        section: str,
        content: str,
    ) -> str:
        """保存记忆。主动调用！不要等用户要求！不要等用户说"请保存"或"帮我记住"！

        WHEN TO SAVE —— 遇到以下情况立即调用：
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
          用户提到具体、有时限的目标（如"想6月前找到实习"）
            → entity_type='goals', section=目标标题, content=具体计划

        注意：
        - 职业方向/目标岗位（"我想做C++开发/产品/算法"）→ 用 update_profile(target_direction=...) 而非此工具
        - 学校/专业/年级等画像字段 → 用 update_profile
        entity_type: skills/experiences/goals/preferences/decisions/status"""
        logger.info("Tool call: memory_save", entity_type=entity_type, section=section)

        if entity_type not in _EVENT_TYPE_MAP:
            return f"未知的类型 {entity_type}。支持: {', '.join(_EVENT_TYPE_MAP.keys())}"

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
            ctx.deps.build_context_cache = ""
            return f"已保存 {entity_type}/{section}"
        return f"{entity_type}/{section} 内容未变化，跳过"
