"""记忆工具 — memory_search / memory_save。"""

from __future__ import annotations

from typing import Any

from lib.memory import get_memory
from lib.tools._base import ToolDef, ToolMeta, tool_error
from shared.logging import get_logger

logger = get_logger(__name__)

# entity_type（模型可见）→ 内部事件类型
_EVENT_TYPE_MAP: dict[str, str] = {
    "interests": "interest_observed",
    "values": "value_surfaced",
    "preferences": "preference_learned",
    "emotions": "emotional_pattern",
    "moments": "significant_moment",
    "decisions": "decision_made",
    "reflections": "reflection_added",
    "contradictions": "contradiction_noted",
    "relationships": "relationship_noted",
    "profile": "profile_updated",
}


async def _search(args: dict[str, Any], deps) -> str:
    query = args.get("query", "").strip()
    if not query:
        return tool_error("请提供搜索关键词")

    memory = get_memory()
    items = await memory.recall(
        deps.user_id,
        query,
        search_mode=args.get("search_mode", "keyword"),
        time_filter=args.get("time_filter"),
    )
    if items:
        return "\n".join(f"- [{item.categories[0] if item.categories else '?'}] {item.content[:300]}" for item in items)
    return "未找到相关内容。"


async def _save(args: dict[str, Any], deps) -> str:
    entity_type = args.get("entity_type", "")
    section = args.get("section", "")
    content = args.get("content", "")

    if entity_type not in _EVENT_TYPE_MAP:
        return tool_error(
            f"未知类型 '{entity_type}'。支持: {', '.join(_EVENT_TYPE_MAP)}",
            "INVALID_ENTITY_TYPE",
        )

    payload: dict[str, Any] = {"key": section, "value": content, "source": "Agent工具"}
    if entity_type == "decisions":
        payload = {"title": section, "content": content}
    elif entity_type == "moments":
        payload = {"title": section, "description": content}
    elif entity_type == "relationships":
        payload = {"person": section, "description": content}

    memory = get_memory()
    event = await memory.remember(
        user_id=deps.user_id,
        event_type=_EVENT_TYPE_MAP[entity_type],
        entity_type=entity_type,
        entity_id=section,
        payload=payload,
        source="Agent工具",
        db=deps.db,
    )
    if event and event.id is not None:
        deps.pending_event_ids.append(str(event.id))
        deps.build_context_cache = ""
        return f"已记录 {entity_type}/{section}"

    return f"{entity_type}/{section} 内容未变化，跳过"


def create_memory_tools() -> list[ToolDef]:
    entity_types = " / ".join(_EVENT_TYPE_MAP)
    return [
        ToolDef(
            name="memory_search",
            description=(
                "搜索记忆。"
                "search_mode: keyword（默认，关键词搜索）或 grep（时间范围浏览，需配合 time_filter）。"
                "time_filter: today / yesterday / recent_3d / recent_7d / recent_30d / YYYY-MM-DD~YYYY-MM-DD。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或时间描述"},
                    "search_mode": {"type": "string", "description": "keyword（默认）或 grep", "default": "keyword"},
                    "time_filter": {"type": "string", "description": "时间过滤（today/yesterday/recent_7d 等）"},
                },
                "required": ["query"],
            },
            execute=_search,
            read_only=True,
            meta=ToolMeta(always_on=True, risk="read-only", search_hint="搜索记忆、回忆、查找历史"),
        ),
        ToolDef(
            name="memory_save",
            description=("保存记忆。主动调用，不要等用户要求。" f"entity_type: {entity_types}"),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_type": {"type": "string", "description": f"类型: {entity_types}"},
                    "section": {"type": "string", "description": "标题/名称"},
                    "content": {"type": "string", "description": "具体内容"},
                },
                "required": ["entity_type", "section", "content"],
            },
            execute=_save,
            read_only=False,
            meta=ToolMeta(always_on=True, risk="write", search_hint="保存记忆、记录、记住"),
        ),
    ]
