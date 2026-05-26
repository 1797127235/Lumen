from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import ToolReturn


@dataclass
class ToolMeta:
    """工具元数据，支撑动态工具发现的分层决策。"""

    risk: str = "read-only"  # "read-only" | "write" | "destructive"
    always_on: bool = False
    search_hint: str | None = None  # 搜索别名/口语化表达
    tags: list[str] = field(default_factory=list)


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True
    execute: Callable[..., Any] | None = None
    label: str = ""
    meta: ToolMeta = field(default_factory=ToolMeta)


def tool_ok(text: str, **metadata: Any) -> ToolReturn:
    """工具成功返回。

    text 发给 LLM；metadata 仅应用层可见（日志、追踪）。
    """
    return ToolReturn(return_value=text, metadata=metadata if metadata else None)


def tool_error(message: str, code: str = "", **metadata: Any) -> ToolReturn:
    """工具错误返回。

    LLM 看到 ❌ 前缀 + 错误信息；code / metadata 仅应用层可见。
    """
    prefix = f"❌[{code}]" if code else "❌"
    meta: dict[str, Any] = {"error": True}
    if code:
        meta["code"] = code
    meta.update(metadata)
    return ToolReturn(return_value=f"{prefix} {message}", metadata=meta)
