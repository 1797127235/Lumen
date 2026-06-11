"""Tool Loop Guard — 检测并截断重复工具调用（语义签名）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_REPEAT_LIMIT = 3
_EXCLUDED_TOOLS = frozenset({"task_output", "task_stop"})

_SEMANTIC_COMMAND_TOOLS = frozenset({"shell"})
_SEMANTIC_FILE_TOOLS = frozenset({"file_edit", "file_read", "file_grep"})
_SEMANTIC_PATH_KEY = {"file_edit": "file_path", "file_read": "file_path", "file_grep": "pattern"}


@dataclass
class _LoopState:
    signature: str = ""
    repeat_count: int = 0


class ToolLoopGuard:
    """
    按 conversation 跟踪工具调用签名，检测连续重复。

    使用语义签名：shell 工具取 command 前缀，file 工具取 file_path，
    其他工具保持精确匹配。语义相同的调用即使参数微调也会被识别为重复。
    """

    def __init__(self, repeat_limit: int = _DEFAULT_REPEAT_LIMIT) -> None:
        self._repeat_limit = max(2, repeat_limit)
        self._states: dict[str, _LoopState] = {}

    def _signature(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name in _SEMANTIC_COMMAND_TOOLS:
            cmd = str(arguments.get("command", ""))[:100]
            return f"{tool_name}:{cmd}"
        if tool_name in _SEMANTIC_FILE_TOOLS:
            key = _SEMANTIC_PATH_KEY.get(tool_name, "file_path")
            path = str(arguments.get(key, ""))
            return f"{tool_name}:{path}"
        args = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            default=lambda o: None,
        )
        return f"{tool_name}:{args}"

    def check_and_record(
        self,
        conversation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[bool, str]:
        """
        记录本次调用，返回 (should_block, reason)。

        should_block=True 表示已达到重复上限，应拒绝执行。
        """
        if tool_name in _EXCLUDED_TOOLS:
            return False, ""

        sig = self._signature(tool_name, arguments)
        key = f"{conversation_id}:{sig}"
        state = self._states.get(key)

        if state is None:
            self._states[key] = _LoopState(signature=sig, repeat_count=1)
            return False, ""

        state.repeat_count += 1
        if state.repeat_count < self._repeat_limit:
            return False, ""

        reason = (
            f"[ToolLoopGuard] 工具 '{tool_name}' 以相似参数连续调用了 "
            f"{state.repeat_count} 次，已触发循环保护。\n"
            f"请停止重复尝试，直接向用户说明当前进度或失败原因。"
        )
        logger.warning(
            "tool_loop_guard triggered",
            conversation_id=conversation_id,
            tool=tool_name,
            repeat_count=state.repeat_count,
            signature=sig[:80],
        )
        return True, reason

    def reset(self, conversation_id: str) -> None:
        """清理指定 conversation 的状态（对话结束时调用）。"""
        keys_to_remove = [k for k in self._states if k.startswith(f"{conversation_id}:")]
        for k in keys_to_remove:
            del self._states[k]


# 进程级单例
_loop_guard_singleton: ToolLoopGuard | None = None


def get_loop_guard() -> ToolLoopGuard:
    global _loop_guard_singleton
    if _loop_guard_singleton is None:
        _loop_guard_singleton = ToolLoopGuard()
    return _loop_guard_singleton
