"""Agent 工具模块 — 统一工具系统入口（新架构）。

使用方式：
  from backend.modules.agent.tools.core.factory import create_tool_runtime
  registry, dispatcher, resolver = create_tool_runtime()

内部通过 Registry + Dispatcher 管理工具，不再依赖 AST 扫描。
"""

from backend.modules.agent.tools.core import (
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
    ToolRuntimeContext,
    ToolsetResolver,
)
from backend.modules.agent.tools.core.factory import create_tool_runtime

__all__ = [
    "ToolDefinition",
    "ToolDispatcher",
    "ToolRegistry",
    "ToolRuntimeContext",
    "ToolsetResolver",
    "create_tool_runtime",
]
