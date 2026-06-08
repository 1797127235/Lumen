"""工具总装配线 — 动态按需加载版本。

改造前：assemble_tools() 返回全量 list[ToolDef]
改造后：
  1. register_all_tools() 将所有工具注册到全局 ToolRegistry
  2. assemble_visible_tools(conversation_id) 返回当前 conversation 可见工具
  3. build_deferred_tools_hint() 生成 deferred 目录字符串注入 system prompt
"""

from __future__ import annotations

from lib.tools._base import ToolDef
from lib.tools._discovery import get_tool_discovery_state
from lib.tools._middleware import wrap_with_budget, wrap_with_failure_degradation, wrap_with_logging
from lib.tools._registry import ToolRegistry, get_tool_registry
from lib.tools._search_tool import create_tool_search
from lib.tools.delegate import create_delegate_tools
from lib.tools.files import create_file_tools
from lib.tools.memory import create_memory_tools
from lib.tools.profile import create_profile_tools
from lib.tools.shell import create_shell_tools
from lib.tools.skill_load import create_skill_tools
from lib.tools.vision import create_vision_tools
from lib.tools.web_search import create_web_search_tools
from lib.tools.web_tools import create_web_tools
from shared.logging import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  注册阶段（应用启动 / Agent 重建时调用一次）
# ═══════════════════════════════════════════════════════════════════


def register_all_tools() -> ToolRegistry:
    """将所有工具注册到全局 ToolRegistry，应用中间件。返回 Registry 单例。"""
    registry = get_tool_registry()
    # 清空后重新注册（支持 MCP 动态增删后重建）
    for name in list(registry.get_registered_names()):
        registry.unregister(name)

    all_tools: list[ToolDef] = [
        *create_file_tools(),
        *create_memory_tools(),
        *create_profile_tools(),
        *create_web_tools(),  # 多后端搜索 + 提取 + 爬取
        *create_web_search_tools(),  # 原有搜索工具
        *create_shell_tools(),
        *create_skill_tools(),
        *create_delegate_tools(),  # 新增：delegate 子 Agent 工具
        *create_vision_tools(),  # 新增：Vision 图片分析工具
        create_tool_search(),
    ]

    # 应用中间件（连续失败降级 → 日志 → 预算）
    # 注意：failure_degradation 必须在最内层，这样它检测的是工具的真实返回
    all_tools = wrap_with_failure_degradation(all_tools)
    all_tools = wrap_with_logging(all_tools)
    all_tools = wrap_with_budget(all_tools, limit=20)

    for tool in all_tools:
        registry.register(tool, source_type="builtin", source_name="")

    _register_mcp_tools(registry)
    logger.info("工具注册完成", total=len(registry.get_registered_names()))
    return registry


def _register_mcp_tools(registry: ToolRegistry) -> None:
    try:
        from lib.tools.mcp.tool_bridge import discover_mcp_tools

        mcp_tools = discover_mcp_tools()
        if not mcp_tools:
            return

        # 和内置工具一样走中间件（连续失败降级 → 日志 → 预算）
        tools_only = [tool for _, tool in mcp_tools]
        tools_only = wrap_with_failure_degradation(tools_only)
        tools_only = wrap_with_logging(tools_only)
        tools_only = wrap_with_budget(tools_only, limit=20)

        for (server_name, _), tool in zip(mcp_tools, tools_only, strict=False):
            registry.register(tool, source_type="mcp", source_name=server_name)
    except Exception as e:
        logger.warning("MCP 工具注册失败", error=str(e))


# ═══════════════════════════════════════════════════════════════════
#  按需组装阶段（每轮对话时调用）
# ═══════════════════════════════════════════════════════════════════


def assemble_visible_tools(conversation_id: str | None) -> list[ToolDef]:
    """获取当前 conversation 可见的工具列表（always_on + preloaded）。"""
    registry = get_tool_registry()
    discovery = get_tool_discovery_state()

    always_on = registry.get_always_on_names()
    preloaded = set(discovery.get_visible(conversation_id))
    visible_names = sorted(always_on | preloaded)

    return [tool for name in visible_names if (tool := registry.get_tool(name)) is not None]


def build_deferred_tools_hint(conversation_id: str | None) -> str:
    """构建 deferred 工具目录字符串，注入 system prompt。"""
    registry = get_tool_registry()
    discovery = get_tool_discovery_state()

    always_on = registry.get_always_on_names()
    preloaded = set(discovery.get_visible(conversation_id))
    visible = always_on | preloaded

    deferred = registry.get_deferred_tools(visible=visible)
    builtin = deferred.get("builtin", [])
    mcp = deferred.get("mcp", {})

    if not builtin and not mcp:
        return ""

    lines = ["\n---\n\n## 可用但未加载的工具\n"]
    lines.append("以下工具当前不可直接调用，但你可以使用 `tool_search` 搜索并加载它们：\n")

    if builtin:
        lines.append("")
        for name, desc in builtin:
            lines.append(f"- {name} — {desc}")

    if mcp:
        for server_name, tools in sorted(mcp.items()):
            lines.append(f"\n**{server_name}**:")
            for name, desc in tools:
                lines.append(f"  - {name} — {desc}")

    lines.append('\n加载方式：`tool_search(query="select:工具名")` 或 `tool_search(query="功能关键词")`')
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  PydanticAI 转换
# ═══════════════════════════════════════════════════════════════════


def build_pydantic_toolset(tools: list[ToolDef]):
    """将 list[ToolDef] 转换为 PydanticAI FunctionToolset。"""
    from pydantic_ai import FunctionToolset  # pyright: ignore[reportMissingImports]

    pydantic_tools = [_to_pydantic_tool(t) for t in tools]
    return FunctionToolset(pydantic_tools)


def build_pydantic_toolset_for_conversation(conversation_id: str | None):
    """为指定 conversation 构建仅包含可见工具的 PydanticAI FunctionToolset。"""
    visible_tools = assemble_visible_tools(conversation_id)
    return build_pydantic_toolset(visible_tools)


def _to_pydantic_tool(t: ToolDef):
    from pydantic_ai import RunContext  # pyright: ignore[reportMissingImports]
    from pydantic_ai.tools import Tool  # pyright: ignore[reportMissingImports]

    from core.agent import LumenDeps

    async def handler(ctx: RunContext[LumenDeps], **kwargs):
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
