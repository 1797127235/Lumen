"""随记与数据源工具 — data_source_search / notes_list。"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from sqlalchemy import select

from lib.memory.models import GrowthEvent
from lib.memory.search import search_all
from lib.tools._base import ToolDef, tool_error
from shared.logging import get_logger

logger = get_logger(__name__)


async def _data_source_search(args: dict[str, Any], deps) -> str:
    query = args.get("query", "").strip()
    limit = min(int(args.get("limit", 5)), 10)
    if not query:
        return tool_error("请提供搜索关键词")
    results = await search_all(deps.user_id, query, limit=limit)
    if not results:
        return "未找到相关内容。"
    lines = [f"找到 {len(results)} 条相关内容："]
    for i, item in enumerate(results, 1):
        lines.append(f"\n{i}. {item.content}")
    return "\n".join(lines)


async def _notes_list(args: dict[str, Any], deps) -> str:
    limit = min(int(args.get("limit", 10)), 50)

    rows = (
        (
            await deps.db.execute(
                select(GrowthEvent)
                .where(GrowthEvent.user_id == deps.user_id)
                .where(GrowthEvent.event_type == "quick_note")
                .order_by(GrowthEvent.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        return "用户目前没有任何随记。"

    lines = [f"共 {len(rows)} 条随记（最近 {limit} 条）："]
    for i, ev in enumerate(rows, 1):
        payload: dict[str, Any] = {}
        if ev.payload_json:
            with contextlib.suppress(json.JSONDecodeError):
                payload = json.loads(ev.payload_json)
        content = payload.get("content", "")
        ts = ev.created_at.strftime("%Y-%m-%d %H:%M") if ev.created_at else "未知时间"
        lines.append(f"\n{i}. [{ts}] {content}")
    return "\n".join(lines)


def create_notes_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="data_source_search",
            description="语义搜索用户的记忆内容（随记、成长事件等）。当用户问到某个话题、技术或想法时使用。",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "limit": {"type": "integer", "description": "最多返回条数", "default": 5},
                },
                "required": ["query"],
            },
            execute=_data_source_search,
            read_only=True,
        ),
        ToolDef(
            name="notes_list",
            description="列出用户最近的随记。当用户问'我记了什么'、'你能看见我的笔记吗'时使用。",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "最多返回条数", "default": 10},
                },
            },
            execute=_notes_list,
            read_only=True,
        ),
    ]
