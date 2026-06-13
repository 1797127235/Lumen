"""MCP 配置工具 — 让 Agent 能安全地管理外部 MCP server。

支持通过 `tool_search` 按需加载。提供对 `~/.lumen/config.json["mcp_servers"]` 的增删改查，
操作后自动刷新 MCP 连接。
"""

from __future__ import annotations

from typing import Any

from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok
from shared.logging import get_logger

logger = get_logger(__name__)


def _serialize_args(args: Any) -> list[str]:
    if isinstance(args, list):
        return [str(a) for a in args]
    if isinstance(args, str):
        return [args]
    return []


def _resolve_lumen_rss_preset(args: dict[str, Any]) -> dict[str, Any]:
    """如果用户指定 preset='lumen-rss'，自动填充内置 RSS server 的配置。"""
    if args.get("preset") != "lumen-rss":
        return args
    resolved = dict(args)
    resolved.setdefault("transport", "stdio")
    resolved.setdefault("command", r"C:\Users\liu\AppData\Local\Programs\Python\Python312\python.exe")
    resolved.setdefault("args", [r"E:\MyHub\Lumen\mcp_servers\rss\server.py"])
    resolved.setdefault("enabled", True)
    resolved.setdefault("auto_approve", True)
    resolved.setdefault("read_only", False)
    return resolved


async def _refresh_and_register() -> None:
    """刷新 MCP 连接并重注册工具到 ToolRegistry。

    refresh() 只重建 McpClientManager 的连接，不触发 ToolRegistry 更新。
    必须显式调用 _register_mcp_tools() 把新发现的工具注册进去，
    否则当前对话中永远无法使用刚连接的 MCP 工具。
    """
    from lib.tools._registry import get_tool_registry
    from lib.tools.factory import _register_mcp_tools
    from lib.tools.mcp.client_manager import get_mcp_manager

    await get_mcp_manager().refresh()
    _register_mcp_tools(get_tool_registry())


async def _tool_mcp_server_manage(args: dict[str, Any], ctx: Any = None) -> str:
    """管理 MCP server 配置。"""
    from lib.tools.mcp.client_manager import get_mcp_manager
    from lib.tools.mcp.config_store import (
        add_mcp_server,
        get_mcp_server,
        load_mcp_servers,
        remove_mcp_server,
        update_mcp_server,
    )
    from lib.tools.mcp.models import McpServerConfig

    args = _resolve_lumen_rss_preset(args)
    action = args.get("action", "list").strip().lower()

    if action == "list":
        servers = load_mcp_servers()
        return tool_ok(
            "已配置 MCP servers:",
            servers=[
                {
                    "name": s.name,
                    "transport": s.transport,
                    "command": s.command,
                    "args": s.args,
                    "url": s.url,
                    "enabled": s.enabled,
                    "state": "connected" if s.enabled else "disabled",
                }
                for s in servers
            ],
        )

    name = args.get("name", "").strip()
    if not name:
        return tool_error("必须提供 name")

    if action == "remove":
        existed = remove_mcp_server(name)
        if not existed:
            return tool_error(f"MCP server '{name}' 不存在")
        await _refresh_and_register()
        return tool_ok(f"已删除 MCP server: {name}")

    if action == "add":
        if get_mcp_server(name) is not None:
            return tool_error(f"MCP server '{name}' 已存在，如需更新请用 action='update'")

        transport = args.get("transport", "stdio").strip()
        command = args.get("command", "").strip()
        raw_args = args.get("args", [])
        args_list = _serialize_args(raw_args)
        url = args.get("url", "").strip()

        if transport == "stdio" and not command:
            return tool_error("transport='stdio' 时必须提供 command")
        if transport == "sse" and not url:
            return tool_error("transport='sse' 时必须提供 url")

        env = args.get("env", {})
        if not isinstance(env, dict):
            return tool_error("env 必须是字典")

        config = McpServerConfig(
            name=name,
            transport=transport,
            command=command,
            args=args_list,
            url=url,
            env={k: str(v) for k, v in env.items()},
            enabled=bool(args.get("enabled", True)),
            auto_approve=bool(args.get("auto_approve", True)),
            read_only=bool(args.get("read_only", False)),
        )
        add_mcp_server(config)
        await _refresh_and_register()
        return tool_ok(f"已添加并连接 MCP server: {name}", config=config.model_dump())

    if action == "update":
        updates: dict[str, Any] = {}
        for key in ("transport", "command", "args", "url", "env", "enabled", "auto_approve", "read_only"):
            if key in args:
                value = args[key]
                if key == "args":
                    value = _serialize_args(value)
                elif key == "env" and isinstance(value, dict):
                    value = {k: str(v) for k, v in value.items()}
                updates[key] = value

        if not updates:
            return tool_error("没有提供任何更新字段")

        updated = update_mcp_server(name, updates)
        if updated is None:
            return tool_error(f"MCP server '{name}' 不存在")
        await _refresh_and_register()
        return tool_ok(f"已更新 MCP server: {name}", config=updated.model_dump())

    if action == "test":
        await _refresh_and_register()
        server = get_mcp_server(name)
        if server is None:
            return tool_error(f"MCP server '{name}' 未配置")
        statuses = get_mcp_manager().get_status()
        for status in statuses:
            if status.name == name:
                return tool_ok(f"MCP server '{name}' 状态: {status.state}", status=status.model_dump())
        return tool_ok(f"MCP server '{name}' 已配置但尚未连接")

    return tool_error(f"未知 action: {action}，支持 list/add/update/remove/test")


def create_mcp_setup_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="mcp_server_manage",
            description=(
                "管理外部 MCP server 配置。支持增删改查和测试连接，操作后自动刷新连接。\n\n"
                "WHEN TO USE:\n"
                "- 用户要求启用某个 MCP server（如 RSS、搜索、数据库等）\n"
                "- 用户想查看已配置的 MCP servers\n"
                "- 用户想修改/删除某个 MCP server\n\n"
                "ACTIONS:\n"
                "- list: 列出所有已配置 server\n"
                "- add: 新增 server（需提供完整配置）\n"
                "- update: 更新已有 server 的字段\n"
                "- remove: 删除 server\n"
                "- test: 测试连接状态\n\n"
                "Lumen 内置 lumen-rss 的最简启用方式:\n"
                "action='add', name='lumen-rss', preset='lumen-rss'\n\n"
                "完整手动配置示例（Windows）:\n"
                "action='add', name='lumen-rss', transport='stdio', "
                "command='C:\\\\Users\\\\liu\\\\AppData\\\\Local\\\\Programs\\\\Python\\\\Python312\\\\python.exe', "
                "args=['E:\\\\MyHub\\\\Lumen\\\\mcp_servers\\\\rss\\\\server.py'], enabled=true, auto_approve=true, read_only=false\n\n"
                "如果用户只说'启用 RSS'而没有给出路径，优先用 preset='lumen-rss'。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "add", "update", "remove", "test"],
                        "description": "操作类型",
                    },
                    "name": {
                        "type": "string",
                        "description": "server 名称，add/update/remove/test 时必填",
                    },
                    "transport": {
                        "type": "string",
                        "enum": ["stdio", "sse"],
                        "description": "传输协议，add 时默认 stdio",
                    },
                    "command": {
                        "type": "string",
                        "description": "stdio 模式下的可执行命令",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "stdio 模式下的命令参数列表",
                    },
                    "url": {
                        "type": "string",
                        "description": "sse 模式下的 server URL",
                    },
                    "env": {
                        "type": "object",
                        "description": "环境变量键值对",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "是否启用",
                        "default": True,
                    },
                    "auto_approve": {
                        "type": "boolean",
                        "description": "是否自动批准工具调用",
                        "default": True,
                    },
                    "read_only": {
                        "type": "boolean",
                        "description": "是否标记为只读工具",
                        "default": False,
                    },
                    "preset": {
                        "type": "string",
                        "enum": ["lumen-rss"],
                        "description": "内置 server 预设。设为 'lumen-rss' 时自动填充 RSS server 的配置",
                    },
                },
                "required": ["action"],
            },
            execute=_tool_mcp_server_manage,
            read_only=False,
            meta=ToolMeta(
                always_on=True,
                risk="write",
                search_hint="配置 MCP server、启用 RSS、添加 MCP server、连接 MCP",
            ),
        ),
    ]
