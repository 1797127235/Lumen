"""记忆工具 Handlers — 搜索和保存用户记忆。"""

from __future__ import annotations

from typing import Any

from backend.core.logging import get_logger
from backend.modules.agent.tools.builtin.schemas import MemorySaveArgs, MemorySearchArgs
from backend.modules.agent.tools.core.context import ToolRuntimeContext
from backend.modules.memory import get_memory

logger = get_logger(__name__)


async def handle_memory_search(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """搜索用户记忆。"""
    typed: MemorySearchArgs = args  # type: ignore[assignment]
    query = typed["query"]
    search_mode = typed.get("search_mode", "keyword")
    time_filter = typed.get("time_filter")

    if not query or not query.strip():
        return "[工具错误] 请提供搜索关键词。"

    memory_instance = get_memory()
    items = await memory_instance.recall(
        ctx.user_id,
        query,
        search_mode=search_mode,
        time_filter=time_filter,
    )
    if items:
        data = "\n".join(f"- [{item.categories[0] if item.categories else '?'}] {item.content[:300]}" for item in items)
        return data

    return "未找到相关内容。"


_EVENT_TYPE_MAP: dict[str, str] = {
    "skills": "skill_added",
    "experiences": "experience_added",
    "preferences": "preference_learned",
    "goals": "goal_updated",
    "decisions": "decision_made",
    "status": "status_changed",
    "note": "note_added",
}


async def handle_memory_save(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """保存记忆。"""
    typed: MemorySaveArgs = args  # type: ignore[assignment]
    entity_type = typed["entity_type"]
    section = typed["section"]
    content = typed["content"]

    if entity_type not in _EVENT_TYPE_MAP:
        return f"[工具错误/INVALID_ENTITY_TYPE] 未知的类型 {entity_type}。支持: {', '.join(_EVENT_TYPE_MAP.keys())}"

    from backend.modules.profile.schemas import (
        DecisionPayload,
        ExperiencePayload,
        KeyValuePayload,
        SkillPayload,
    )

    if entity_type == "skills":
        payload = SkillPayload(name=section, level="familiar", context=content, source="Agent工具").model_dump()
    elif entity_type == "experiences":
        payload = ExperiencePayload(title=section, description=content, source="Agent工具").model_dump()
    elif entity_type == "decisions":
        payload = DecisionPayload(title=section, content=content).model_dump()
    elif entity_type in ("preferences", "goals", "status", "note"):
        payload = KeyValuePayload(key=section, value=content).model_dump()
    else:
        return f"[工具错误/UNSUPPORTED_TYPE] 不支持的类型 {entity_type}"

    memory = get_memory()
    event = await memory.remember(
        user_id=ctx.user_id,
        event_type=_EVENT_TYPE_MAP[entity_type],
        entity_type=entity_type,
        entity_id=section,
        payload=payload,
        source="Agent工具",
        db=ctx.db,
    )
    if event and event.id is not None:
        # 将 event_id 记录到 context 中，供后续使用
        pending = ctx.tool_state.setdefault("pending_event_ids", [])
        pending.append(str(event.id))
        ctx.tool_state["build_context_cache"] = ""
        return f"已保存 {entity_type}/{section}"

    return f"{entity_type}/{section} 内容未变化，跳过"
