"""MCP Client 管理器 — 全局单例，维护所有 Server 连接与工具发现。"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession

from lib.tools._base import tool_error, tool_ok
from lib.tools.mcp.config_store import load_mcp_servers
from lib.tools.mcp.models import McpServerConfig, McpServerStatus
from lib.tools.mcp.transport import create_sse_transport, create_stdio_transport
from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass
class _ServerConnection:
    """单个 Server 的连接状态。"""

    config: McpServerConfig
    session: ClientSession | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    error_msg: str = ""
    _exit_stack: AsyncExitStack | None = None

    @property
    def is_connected(self) -> bool:
        return self.session is not None and self.error_msg == ""


class McpClientManager:
    """MCP Client 全局管理器。

    生命周期：
        1. lifespan startup 调用 connect_all()
        2. 每次 Agent 重建前调用 discover_and_register()
        3. lifespan shutdown 调用 disconnect_all()
    """

    def __init__(self) -> None:
        self._connections: dict[str, _ServerConnection] = {}
        self._lock = asyncio.Lock()

    # -- 生命周期 --

    async def connect_all(self) -> None:
        """读取配置并连接所有 enabled server。"""
        configs = load_mcp_servers()
        async with self._lock:
            # 先断开已不存在或禁用的连接
            names_in_config = {c.name for c in configs if c.enabled}
            for name in list(self._connections.keys()):
                if name not in names_in_config:
                    await self._disconnect_one(name)

            for cfg in configs:
                if not cfg.enabled:
                    continue
                if cfg.name in self._connections and self._connections[cfg.name].is_connected:
                    continue
                await self._connect_one(cfg)

    async def disconnect_all(self) -> None:
        """断开所有连接。"""
        async with self._lock:
            for name in list(self._connections.keys()):
                await self._disconnect_one(name)
            self._connections.clear()

    async def refresh(self) -> None:
        """重新加载配置并重建连接。"""
        await self.disconnect_all()
        await self.connect_all()

    # -- 工具发现 --

    def discover_tools(self) -> list[tuple[str, list[dict[str, Any]]]]:
        """发现所有已连接 server 的工具。

        Returns:
            [(server_name, [tool_dict, ...]), ...]
        """
        results: list[tuple[str, list[dict[str, Any]]]] = []
        for name, conn in self._connections.items():
            if conn.is_connected and conn.tools:
                results.append((name, conn.tools))
        return results

    def list_all_tools(self) -> list[dict[str, Any]]:
        """扁平列出所有 MCP 工具（含 server 信息）。"""
        tools: list[dict[str, Any]] = []
        for server_name, server_tools in self.discover_tools():
            for t in server_tools:
                tools.append({"server": server_name, **t})
        return tools

    # -- 工具调用 --

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]):
        """调用指定 server 的 tool。

        Args:
            server_name: Server 名称
            tool_name: MCP Tool 名称
            arguments: 工具参数

        Returns:
            工具结果字符串
        """
        conn = self._connections.get(server_name)
        if conn is None:
            return tool_error(f"Server '{server_name}' 未配置", "MCP")
        if not conn.is_connected:
            return tool_error(f"Server '{server_name}' 未连接: {conn.error_msg}", "MCP")
        if conn.session is None:
            return tool_error(f"Server '{server_name}' Session 未初始化", "MCP")

        try:
            result = await conn.session.call_tool(tool_name, arguments)
            # result.content 是 list[TextContent | ImageContent | EmbeddedResource]
            texts: list[str] = []
            for item in result.content:
                if hasattr(item, "text"):
                    texts.append(item.text)
                elif hasattr(item, "uri"):
                    texts.append(f"[resource: {item.uri}]")
                else:
                    texts.append(str(item))
            return tool_ok("\n".join(texts) if texts else "(空结果)")
        except Exception as exc:
            logger.exception("MCP tool call failed", server=server_name, tool=tool_name, error=str(exc))
            return tool_error(f"调用 '{tool_name}' 失败: {exc}", "MCP")

    # -- 状态查询 --

    def get_status(self) -> list[McpServerStatus]:
        """获取所有 server 状态。"""
        statuses: list[McpServerStatus] = []
        for name, conn in self._connections.items():
            statuses.append(
                McpServerStatus(
                    name=name,
                    state="connected" if conn.is_connected else "error" if conn.error_msg else "disconnected",
                    error_msg=conn.error_msg,
                    tools_count=len(conn.tools),
                )
            )
        # 补充未连接但配置中存在的 server
        configs = load_mcp_servers()
        connected_names = {s.name for s in statuses}
        for cfg in configs:
            if cfg.name not in connected_names:
                statuses.append(
                    McpServerStatus(
                        name=cfg.name,
                        state="disconnected",
                        error_msg="",
                        tools_count=0,
                    )
                )
        return statuses

    def get_server_config(self, name: str) -> McpServerConfig | None:
        """获取指定 server 的原始配置。"""
        conn = self._connections.get(name)
        if conn:
            return conn.config
        return None

    # -- 内部连接管理 --

    async def _connect_one(self, cfg: McpServerConfig) -> None:
        """连接单个 server。"""
        name = cfg.name
        conn = _ServerConnection(config=cfg)
        self._connections[name] = conn

        try:
            stack = AsyncExitStack()
            if cfg.transport == "stdio":
                transport_cm = create_stdio_transport(
                    command=cfg.command,
                    args=cfg.args,
                    env=cfg.env,
                )
            elif cfg.transport == "sse":
                transport_cm = create_sse_transport(url=cfg.url)
            else:
                conn.error_msg = f"未知传输协议: {cfg.transport}"
                return

            read_stream, write_stream = await stack.enter_async_context(transport_cm)
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()

            # 获取工具列表
            tools_result = await session.list_tools()
            conn.tools = [
                {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema}
                for t in (tools_result.tools if hasattr(tools_result, "tools") else [])
            ]

            conn.session = session
            conn._exit_stack = stack
            logger.info("MCP server connected", name=name, transport=cfg.transport, tools=len(conn.tools))
        except Exception as exc:
            conn.error_msg = str(exc)
            logger.warning("MCP server connect failed", name=name, error=str(exc))
            # 清理失败的 stack
            if conn._exit_stack:
                import contextlib

                with contextlib.suppress(Exception):
                    await conn._exit_stack.aclose()
                conn._exit_stack = None

    async def _disconnect_one(self, name: str) -> None:
        """断开单个 server。"""
        conn = self._connections.pop(name, None)
        if conn is None:
            return
        if conn._exit_stack:
            import contextlib

            with contextlib.suppress(Exception):
                await conn._exit_stack.aclose()
        conn.session = None
        conn._exit_stack = None
        logger.info("MCP server disconnected", name=name)


# -- 全局单例 --

_mcp_manager: McpClientManager | None = None


def get_mcp_manager() -> McpClientManager:
    """获取全局 MCP ClientManager 实例。"""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = McpClientManager()
    return _mcp_manager
