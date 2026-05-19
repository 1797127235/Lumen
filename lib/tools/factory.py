"""工具总装配线 — 对应 openhanako engine.js buildTools()。"""

from __future__ import annotations

from lib.tools._base import ToolDef
from lib.tools._middleware import wrap_with_budget, wrap_with_logging
from lib.tools.memory import create_memory_tools
from lib.tools.notes import create_notes_tools
from lib.tools.profile import create_profile_tools
from lib.tools.web_search import create_web_search_tools
from shared.logging import get_logger

logger = get_logger(__name__)


def assemble_tools() -> list[ToolDef]:
    """合并所有来源的工具并应用中间件。"""
    tools: list[ToolDef] = [
        *create_memory_tools(),
        *create_profile_tools(),
        *create_notes_tools(),
        *create_web_search_tools(),
        *_discover_mcp_tools(),
    ]
    tools = wrap_with_logging(tools)
    tools = wrap_with_budget(tools, limit=20)
    return tools


def build_pydantic_toolset(tools: list[ToolDef]):
    """将 list[ToolDef] 转换为 PydanticAI FunctionToolset。"""
    from pydantic_ai import FunctionToolset  # pyright: ignore[reportMissingImports]

    pydantic_tools = [_to_pydantic_tool(t) for t in tools]
    return FunctionToolset(pydantic_tools)


def _to_pydantic_tool(t: ToolDef):
    from pydantic_ai import RunContext  # pyright: ignore[reportMissingImports]
    from pydantic_ai.tools import Tool  # pyright: ignore[reportMissingImports]

    from core.agent import LumenDeps

    async def handler(ctx: RunContext[LumenDeps], **kwargs) -> str:
        return await t.execute(kwargs, ctx.deps)

    handler.__name__ = t.name
    handler.__doc__ = t.description

    return Tool.from_schema(
        function=handler,
        name=t.name,
        description=t.description,
        json_schema=t.input_schema,
        takes_ctx=True,
        sequential=not t.read_only,
    )


def _discover_mcp_tools() -> list[ToolDef]:
    try:
        from lib.tools.mcp.tool_bridge import discover_mcp_tools

        return discover_mcp_tools()
    except Exception as e:
        logger.warning("MCP tools unavailable", error=str(e))
        return []
