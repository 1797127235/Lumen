"""PydanticAI Agent 流事件处理器"""

from __future__ import annotations

import json
from typing import Any

from backend.core.logging import get_logger

logger = get_logger(__name__)


class EventHandlers:
    """PydanticAI Agent 流事件处理器

    处理 function_tool_call、function_tool_result、part_delta、agent_run_result 四种事件，
    更新对话状态并生成 SSE 事件。
    """

    @staticmethod
    def function_tool_call(event, state, deps: dict[str, Any]) -> list[dict]:
        state.step += 1
        args = getattr(event, "args", None) or getattr(getattr(event, "part", None), "args", None)
        args_str = _safe_json(args, 300)
        tool_name = getattr(event, "tool_name", None) or getattr(getattr(event, "part", None), "tool_name", None) or ""
        state.trace_records.append(
            {
                "step_number": state.step,
                "step_type": "tool_call",
                "tool_name": tool_name,
                "tool_args": args if isinstance(args, dict) else {"raw": str(args or "")},
                "content": args_str,
            }
        )
        return [{"type": "trace", "kind": "call", "tool": tool_name, "content": args_str}]

    @staticmethod
    def function_tool_result(event, state, deps: dict[str, Any]) -> list[dict]:
        state.step += 1
        content = _normalize_content(getattr(event, "content", None))
        content_display = _truncate(content, 500)
        tool_name = getattr(event, "tool_name", None) or getattr(getattr(event, "part", None), "tool_name", None) or ""
        state.trace_records.append(
            {
                "step_number": state.step,
                "step_type": "tool_result",
                "tool_name": tool_name,
                "tool_result": content[:5000] if isinstance(content, str) else str(content)[:5000],
                "content": content_display,
            }
        )
        return [{"type": "trace", "kind": "result", "tool": tool_name, "content": content_display}]

    @staticmethod
    def part_start(event, state, deps: dict[str, Any]) -> list[dict]:
        """处理 PartStartEvent — 新 part 的首段内容在这里发出。"""
        from pydantic_ai.messages import TextPart, ThinkingPart

        part = event.part
        if isinstance(part, TextPart) and part.content:
            state.full_content += part.content
            return [{"type": "token", "content": part.content, "conversation_id": deps["conversation_id"]}]
        elif isinstance(part, ThinkingPart) and part.content:
            return [{"type": "thinking", "content": part.content, "conversation_id": deps["conversation_id"]}]
        return []

    @staticmethod
    def part_delta(event, state, deps: dict[str, Any]) -> list[dict]:
        from pydantic_ai.messages import TextPartDelta, ThinkingPartDelta

        delta = event.delta
        if isinstance(delta, TextPartDelta):
            text = delta.content_delta or ""
            state.full_content += text
            return [{"type": "token", "content": text, "conversation_id": deps["conversation_id"]}]
        elif isinstance(delta, ThinkingPartDelta):
            text = delta.content_delta or ""
            return [{"type": "thinking", "content": text, "conversation_id": deps["conversation_id"]}]
        return []

    @staticmethod
    def agent_run_result(event, state, deps: dict[str, Any]) -> list[dict]:
        state.new_msgs = event.result.new_messages()
        if not state.cancelled:
            try:
                u = event.result.usage()
                state.usage_data = {"input": u.request_tokens or 0, "output": u.response_tokens or 0}
            except Exception:
                pass
        return []


def _safe_json(value: Any, max_len: int = 0) -> str:
    try:
        s = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value or "")
    except (TypeError, ValueError):
        s = str(value or "")
    if max_len and len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


def _normalize_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


EVENT_HANDLERS: dict[str, Any] = {
    "function_tool_call": EventHandlers.function_tool_call,
    "function_tool_result": EventHandlers.function_tool_result,
    "part_start": EventHandlers.part_start,
    "part_delta": EventHandlers.part_delta,
    "agent_run_result": EventHandlers.agent_run_result,
}
