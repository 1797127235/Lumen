"""SSE 协议格式化器

集中管理 SSE 事件格式化逻辑，解耦协议处理与业务逻辑。
"""

from __future__ import annotations

import json


class SSEFormatter:
    """SSE 协议格式化器"""

    TYPE_TOKEN = "token"
    TYPE_THINKING = "thinking"
    TYPE_TRACE = "trace"
    TYPE_DONE = "done"
    TYPE_ERROR = "error"
    TYPE_SUBAGENT_PROGRESS = "subagent_progress"

    @staticmethod
    def format_event(event_type: str, data: dict) -> str:
        """格式化 SSE 事件"""
        payload = {"type": event_type, **data}
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @staticmethod
    def format_token(content: str) -> str:
        """格式化 token 事件"""
        return SSEFormatter.format_event(
            SSEFormatter.TYPE_TOKEN,
            {"content": content},
        )

    @staticmethod
    def format_thinking(content: str) -> str:
        """格式化 thinking 事件"""
        return SSEFormatter.format_event(
            SSEFormatter.TYPE_THINKING,
            {"content": content},
        )

    @staticmethod
    def format_trace(kind: str, tool: str, content: str) -> str:
        """格式化 trace 事件"""
        return SSEFormatter.format_event(
            SSEFormatter.TYPE_TRACE,
            {"kind": kind, "tool": tool, "content": content},
        )

    @staticmethod
    def format_done(content: str, conversation_id: str) -> str:
        """格式化 done 事件"""
        return SSEFormatter.format_event(
            SSEFormatter.TYPE_DONE,
            {"content": content, "conversation_id": conversation_id},
        )

    @staticmethod
    def format_error(message: str) -> str:
        """格式化 error 事件"""
        return SSEFormatter.format_event(
            SSEFormatter.TYPE_ERROR,
            {"message": message},
        )

    @staticmethod
    def format_subagent_progress(phase: str, detail: str) -> str:
        """格式化子 Agent 进度事件"""
        return SSEFormatter.format_event(
            SSEFormatter.TYPE_SUBAGENT_PROGRESS,
            {"phase": phase, "detail": detail},
        )
