"""PydanticAI 适配层 — 将新工具运行时桥接到 PydanticAI Agent。"""

from __future__ import annotations

from typing import Any

from pydantic_ai import FunctionToolset, RunContext  # pyright: ignore[reportMissingImports]
from pydantic_ai.tools import Tool  # pyright: ignore[reportMissingImports]

from backend.core.logging import get_logger
from backend.modules.agent.deps import LumenDeps
from backend.modules.agent.tools.core import (
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
    ToolRuntimeContext,
    ToolsetResolver,
)

logger = get_logger(__name__)


class PydanticAIToolAdapter:
    """
    将新 ToolRegistry + ToolDispatcher 适配为 PydanticAI FunctionToolset。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        dispatcher: ToolDispatcher,
        resolver: ToolsetResolver,
    ) -> None:
        self.registry = registry
        self.dispatcher = dispatcher
        self.resolver = resolver

    def build_toolset(self, toolset_names: list[str] | None = None) -> FunctionToolset:
        """为 PydanticAI Agent 生成 FunctionToolset。
        Args:
            toolset_names: 要暴露的 toolset 列表，None 表示全部已注册工具
        """
        if toolset_names is None:
            # 暴露所有已注册工具
            tools = self.registry.all_tools()
        else:
            # 通过 resolver 解析 toolset -> 工具名集合
            allowed_names = self.resolver.resolve_many(toolset_names)
            tools = {name: self.registry.get(name) for name in allowed_names if self.registry.get(name) is not None}

        ftools: list[Tool] = []
        for tool_def in tools.values():
            ftools.append(self._create_pydantic_tool(tool_def))

        return FunctionToolset(ftools)

    def _create_pydantic_tool(self, tool_def: ToolDefinition) -> Tool:
        """从 ToolDefinition 创建 PydanticAI Tool。"""

        async def _handler(ctx: RunContext[LumenDeps], **kwargs: Any) -> str:
            rt_ctx = self._make_runtime_context(ctx, tool_def.name)
            try:
                return await self.dispatcher.dispatch(tool_def.name, kwargs, rt_ctx)
            finally:
                # 将 tool_state / usage_budget / trace_sink / pending_event_ids 写回 deps
                self._sync_runtime_context_back(ctx.deps, rt_ctx)

        # 保持函数名和文档，让 PydanticAI 正确识别
        _handler.__name__ = tool_def.name
        _handler.__doc__ = tool_def.description
        return Tool.from_schema(
            function=_handler,
            name=tool_def.name,
            description=tool_def.description,
            json_schema=tool_def.input_schema or {"type": "object", "properties": {}},
            takes_ctx=True,
            sequential=not tool_def.read_only,  # 写操作串行
        )

    def _make_runtime_context(
        self,
        ctx: RunContext[LumenDeps],
        tool_name: str,
    ) -> ToolRuntimeContext:
        """从 PydanticAI 的 RunContext 构造 ToolRuntimeContext。

        关键：从 LumenDeps 读取已有的 tool_state / usage_budget / trace_sink，
        保证跨工具调用状态持续。
        """
        deps = ctx.deps

        # workspace_root: 优先从 LumenDeps 获取，否则自动检测
        workspace_root = getattr(deps, "workspace_root", None)
        if workspace_root is None:
            from backend.shared.path_utils import find_project_root

            workspace_root = find_project_root()

        return ToolRuntimeContext(
            session_id=deps.conversation_id or "",
            conversation_id=deps.conversation_id or "",
            user_id=deps.user_id,
            workspace_root=workspace_root,
            cwd=workspace_root,
            db=deps.db,
            tool_state=dict(getattr(deps, "tool_state", {})),
            usage_budget=dict(getattr(deps, "usage_budget", {})),
            trace_sink=list(getattr(deps, "trace_sink", [])),
            request_metadata={
                "agent_generation": deps.agent_generation,
                "tool_name": tool_name,
            },
        )

    @staticmethod
    def _sync_runtime_context_back(deps: LumenDeps, rt_ctx: ToolRuntimeContext) -> None:
        """将 ToolRuntimeContext 的状态写回 LumenDeps，保证跨调用持续。"""
        deps.tool_state = dict(rt_ctx.tool_state)
        deps.usage_budget = dict(rt_ctx.usage_budget)
        deps.trace_sink = list(rt_ctx.trace_sink)
        # pending_event_ids: memory/profile handlers 写入 rt_ctx.tool_state["pending_event_ids"]
        pending = rt_ctx.tool_state.get("pending_event_ids", [])
        if pending:
            existing = set(deps.pending_event_ids)
            for pid in pending:
                if pid not in existing:
                    deps.pending_event_ids.append(pid)
