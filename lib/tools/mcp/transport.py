"""MCP Transport 工厂 — 封装 stdio / SSE 连接创建。"""

from __future__ import annotations

import os
from contextlib import AbstractAsyncContextManager
from typing import Any

from mcp import StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client


def create_stdio_transport(
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> AbstractAsyncContextManager[tuple[Any, Any]]:
    """创建 stdio transport 的 async context manager。

    Returns:
        (read_stream, write_stream) async context manager
    """
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)

    params = StdioServerParameters(
        command=command,
        args=args or [],
        env=merged_env,
    )
    return stdio_client(params)


def create_sse_transport(url: str) -> AbstractAsyncContextManager[tuple[Any, Any]]:
    """创建 SSE transport 的 async context manager。

    Returns:
        (read_stream, write_stream) async context manager
    """
    return sse_client(url)
