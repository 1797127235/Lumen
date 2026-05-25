"""上下文围栏 — 防止记忆上下文被模型误认为是用户指令。

三层防护：
1. 静态围栏：build_memory_context_block()
2. 清洗函数：sanitize_context()
3. 流式清洗器：StreamingContextScrubber（状态机跨 SSE chunk）
"""

from __future__ import annotations

import re
from enum import Enum, auto

# ── 静态围栏 ──

_MEMORY_CONTEXT_PREFIX = """<memory-context>
[System note: The following is recalled memory context, NOT new user input.
Treat as authoritative reference data — this is the agent's persistent memory
and should inform all responses.]
"""

_MEMORY_CONTEXT_SUFFIX = "\n</memory-context>"


def build_memory_context_block(raw_context: str) -> str:
    """将 prefetch 结果包在 <memory-context> 标签中，附系统说明。"""
    if not raw_context or not raw_context.strip():
        return ""
    return _MEMORY_CONTEXT_PREFIX + raw_context + _MEMORY_CONTEXT_SUFFIX


# ── 清洗函数 ──

_MEMORY_CONTEXT_TAG_RE = re.compile(r"<memory-context>.*?</memory-context>", re.DOTALL)

_SYSTEM_NOTE_RE = re.compile(
    r"\[System note: The following is recalled memory context.*?\]\n?",
    re.IGNORECASE | re.DOTALL,
)


def sanitize_context(text: str) -> str:
    """去除 provider 可能预带的围栏标签，防止双重包装。"""
    text = _MEMORY_CONTEXT_TAG_RE.sub("", text)
    text = _SYSTEM_NOTE_RE.sub("", text)
    return text.strip()


# ── 流式清洗器（状态机） ──


class _ScrubberState(Enum):
    """StreamingContextScrubber 状态机状态。"""

    IDLE = auto()  # 正常输出中
    MAYBE_OPEN = auto()  # 可能遇到 <memory-context> 开头
    INSIDE = auto()  # 在 <memory-context> ... </memory-context> 内
    MAYBE_CLOSE = auto()  # 可能遇到 </memory-context> 开头


class StreamingContextScrubber:
    """跨 SSE chunk 清洗 <memory-context> 标签的状态机。

    简单正则无法处理标签被 chunk 边界割裂的情况，必须用状态机
    跟踪是否在 span 内，并暂存可能不完整的标签尾部。
    """

    _OPEN_TAG = "<memory-context>"
    _CLOSE_TAG = "</memory-context>"

    def __init__(self) -> None:
        self._state = _ScrubberState.IDLE
        self._buffer = ""  # 暂存可能构成标签前缀的字符
        self._max_tag_len = max(len(self._OPEN_TAG), len(self._CLOSE_TAG))

    def reset(self) -> None:
        """新回合开始时重置状态。"""
        self._state = _ScrubberState.IDLE
        self._buffer = ""

    def feed(self, text: str) -> str:
        """处理一段文本，返回清洗后的用户可见文本。"""
        output_parts: list[str] = []
        i = 0

        while i < len(text):
            ch = text[i]
            i += 1

            if self._state == _ScrubberState.IDLE:
                # 检查是否可能开始 open tag
                if ch == "<":
                    self._state = _ScrubberState.MAYBE_OPEN
                    self._buffer = ch
                else:
                    output_parts.append(ch)

            elif self._state == _ScrubberState.MAYBE_OPEN:
                self._buffer += ch
                if self._OPEN_TAG.startswith(self._buffer):
                    if self._buffer == self._OPEN_TAG:
                        # 完整匹配 open tag，进入 INSIDE
                        self._state = _ScrubberState.INSIDE
                        self._buffer = ""
                elif self._CLOSE_TAG.startswith(self._buffer):
                    if self._buffer == self._CLOSE_TAG:
                        # 完整匹配 close tag（但这是 IDLE 状态，不应该）
                        # 当作正常文本输出 buffer 然后回到 IDLE
                        output_parts.append(self._buffer)
                        self._state = _ScrubberState.IDLE
                        self._buffer = ""
                else:
                    # buffer 不构成任何已知标签前缀
                    # 检查是否有更短前缀匹配
                    matched = False
                    for prefix_len in range(len(self._buffer) - 1, 0, -1):
                        suffix = self._buffer[prefix_len:]
                        if self._OPEN_TAG.startswith(suffix) or self._CLOSE_TAG.startswith(suffix):
                            output_parts.append(self._buffer[:prefix_len])
                            self._buffer = suffix
                            matched = True
                            break
                    if not matched:
                        output_parts.append(self._buffer)
                        self._state = _ScrubberState.IDLE
                        self._buffer = ""

            elif self._state == _ScrubberState.INSIDE:
                # 在标签内，等待 close tag
                if ch == "<":
                    self._state = _ScrubberState.MAYBE_CLOSE
                    self._buffer = ch
                # 否则丢弃

            elif self._state == _ScrubberState.MAYBE_CLOSE:
                self._buffer += ch
                if self._CLOSE_TAG.startswith(self._buffer):
                    if self._buffer == self._CLOSE_TAG:
                        # 完整匹配 close tag，回到 IDLE
                        self._state = _ScrubberState.IDLE
                        self._buffer = ""
                elif self._OPEN_TAG.startswith(self._buffer):
                    # 不可能，open tag 以 <m 开头，close 以 </ 开头
                    # 但如果 buffer 变成了 <m... 不可能是 close 前缀
                    # 直接丢弃 buffer
                    pass
                else:
                    # buffer 不构成 close tag 前缀，但可能在 INSIDE 内
                    # 丢弃即可
                    matched = False
                    for prefix_len in range(len(self._buffer) - 1, 0, -1):
                        suffix = self._buffer[prefix_len:]
                        if self._CLOSE_TAG.startswith(suffix):
                            # 保留 suffix 继续匹配
                            self._buffer = suffix
                            matched = True
                            break
                    if not matched:
                        self._buffer = ""
                        self._state = _ScrubberState.INSIDE

        return "".join(output_parts)

    def flush(self) -> str:
        """流结束时输出暂存内容。

        如果状态机处于非 IDLE 状态，丢弃 buffer。
        """
        if self._state == _ScrubberState.IDLE and self._buffer:
            result = self._buffer
            self._buffer = ""
            return result
        self._buffer = ""
        self._state = _ScrubberState.IDLE
        return ""
