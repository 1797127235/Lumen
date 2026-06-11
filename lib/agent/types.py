"""Agent 共享类型 — 避免循环导入。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentContext:
    """Agent 运行时上下文，直接透传给工具。"""

    user_id: str
    db: Any
    conversation_id: str | None = None
    workspace_root: str = ""
    usage_budget: dict[str, Any] = field(default_factory=dict)
    source_platform: str = "web"
    progress_emitter: Callable[[str, str], None] | None = None
    event_bus: Any = None  # EventBus 实例，用于发送工具调用事件
