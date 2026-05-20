from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


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


def tool_ok(text: str) -> str:
    return text


def tool_error(message: str, code: str = "") -> str:
    if code:
        return f"[工具错误/{code}] {message}"
    return f"[工具错误] {message}"
