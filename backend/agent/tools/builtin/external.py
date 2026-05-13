"""外部文档搜索工具 Handler。"""

from __future__ import annotations

from typing import Any

from backend.agent.tools.core.context import ToolRuntimeContext
from backend.logging_config import get_logger
from backend.memory.search import _search_external_fts5

logger = get_logger(__name__)


async def handle_search_external_docs(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """搜索用户本地外部文档（Obsidian 笔记等）。"""

    query = args.get("query", "").strip()
    limit = min(int(args.get("limit", 5)), 10)
    if not query:
        return "[工具错误] 请提供搜索关键词。"
    results = await _search_external_fts5(query, limit, set())
    if not results:
        return "未找到相关外部文档。"
    lines = [f"找到 {len(results)} 条外部文档："]
    for item in results:
        source = item.categories[0] if item.categories else "unknown"
        lines.append(f"[{source}] {item.content[:200]}")
    return "\n".join(lines)
