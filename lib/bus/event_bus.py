from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  事件类型
# ═══════════════════════════════════════════════════════════════


@dataclass
class TurnStarted:
    channel: str
    session_key: str
    chat_id: str
    content: str


@dataclass
class StreamDeltaReady:
    channel: str
    session_key: str
    chat_id: str
    content_delta: str = ""
    thinking_delta: str = ""


@dataclass
class ToolCallStarted:
    channel: str
    session_key: str
    chat_id: str
    call_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class ToolCallCompleted:
    channel: str
    session_key: str
    chat_id: str
    call_id: str
    tool_name: str
    status: str  # "done" | "error"
    result_preview: str = ""


@dataclass
class TraceReady:
    """工具调用/结果 trace 事件，用于前端展示工具调用过程"""

    channel: str
    session_key: str
    chat_id: str
    kind: str  # "call" | "result"
    tool: str
    content: str


@dataclass
class SubagentProgress:
    """子 Agent 进度事件，delegate 工具运行时实时发回。"""

    channel: str
    session_key: str
    chat_id: str
    phase: str  # "started" | "step" | "done" | "error"
    detail: str  # 给用户看的进度文本


# ═══════════════════════════════════════════════════════════════
#  EventBus 实现
# ═══════════════════════════════════════════════════════════════


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable]] = {}

    def on(self, event_type: type, handler: Callable) -> None:
        """订阅事件类型"""
        self._handlers.setdefault(event_type, []).append(handler)

    def emit(self, event: Any) -> None:
        """广播事件（同步触发）"""
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    task = asyncio.create_task(handler(event))
                    del task
                else:
                    handler(event)
            except Exception as e:
                logger.error(f"Event handler error for {event_type.__name__}: {e}")

    async def observe(self, event: Any) -> None:
        """观察事件（异步顺序执行，等待所有 handler 完成）"""
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                logger.error(f"Event observer error for {event_type.__name__}: {e}")
