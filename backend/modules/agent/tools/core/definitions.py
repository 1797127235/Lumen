"""工具定义 — 描述工具是什么，不绑定具体 Agent 框架。"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolDefinition:
    """工具注册表中的标准单元。

    不依赖 FunctionToolset，可被不同 Agent 框架适配成不同形式。
    """

    name: str
    """工具唯一标识（字符串 ID）。"""

    description: str
    """工具描述，注入模型 prompt。"""

    input_schema: dict[str, Any] = field(default_factory=dict)
    """JSON Schema 格式的参数定义。"""

    category: str = "builtin"
    """工具分类：builtin / plugin / mcp / chat 等。"""

    read_only: bool = True
    """是否为只读工具。影响并发策略和权限检查。"""

    requires_approval: bool = False
    """是否需要用户审批（如写文件、执行命令）。"""

    tags: set[str] = field(default_factory=set)
    """额外标签，用于策略路由。"""

    handler: Callable[..., Coroutine[Any, Any, str]] | None = None
    """工具执行函数。Dispatcher 在通过所有策略检查后调用。"""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ToolDefinition.name 不能为空")
        if not self.description:
            raise ValueError("ToolDefinition.description 不能为空")
