from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True
    execute: Callable[..., Any] | None = None
    label: str = ""


def tool_ok(text: str) -> str:
    return text


def tool_error(message: str, code: str = "") -> str:
    if code:
        return f"[工具错误/{code}] {message}"
    return f"[工具错误] {message}"
