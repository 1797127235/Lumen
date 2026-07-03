"""Telegram 通道工具 — 限流器、消息发送

保留核心机制，适配 Lumen 的非流式发送模式。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from telegram import Bot
from telegram import MessageEntity as TgEntity
from telegram.error import NetworkError, RetryAfter, TimedOut

logger = logging.getLogger(__name__)

_TELEGRAM_MSG_LIMIT = 4096

_T = TypeVar("_T")


# ═══════════════════════════════════════════════════════════════════════
#  限流器
# ═══════════════════════════════════════════════════════════════════════


class TelegramOutboundLimiter:
    """Telegram API 限流保护器。

    send/edit/typing 分别控制最小间隔，防止触发 Flood。
    """

    def __init__(
        self,
        *,
        send_interval_s: float = 2.0,
        edit_interval_s: float = 5.0,
        typing_interval_s: float = 8.0,
        global_interval_s: float = 0.25,
        retry_padding_s: float = 1.0,
        max_attempts: int = 5,
    ) -> None:
        self._send_interval_s = send_interval_s
        self._edit_interval_s = edit_interval_s
        self._typing_interval_s = typing_interval_s
        self._global_interval_s = global_interval_s
        self._retry_padding_s = retry_padding_s
        self._max_attempts = max_attempts
        self._chat_locks: dict[int, asyncio.Lock] = {}
        self._typing_locks: dict[int, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._next_chat_at: dict[int, float] = {}
        self._next_typing_at: dict[int, float] = {}
        self._next_global_at = 0.0

    async def run(
        self,
        chat_id: int | str,
        *,
        kind: str,
        label: str,
        action: Callable[[], Awaitable[_T]],
        max_attempts: int | None = None,
    ) -> _T:
        cid = int(chat_id)
        if kind == "typing":
            return await self._run_typing(cid, label=label, action=action)
        attempts = max_attempts or self._max_attempts
        lock = self._chat_locks.setdefault(cid, asyncio.Lock())
        async with lock:
            last_err: Exception | None = None
            for attempt in range(1, attempts + 1):
                await self._wait_for_chat_slot(cid)
                try:
                    result = await self._run_with_global_slot(action)
                    self._mark_used(cid, kind)
                    return result
                except RetryAfter as e:
                    last_err = e
                    delay = max(
                        float(getattr(e, "retry_after", 1.0) or 1.0) + self._retry_padding_s,
                        self._interval(kind),
                    )
                    self._cooldown(cid, delay)
                    logger.warning(
                        "[telegram] %s 命中限流，冷却 chat_id=%s attempt=%d/%d delay=%.1fs",
                        label,
                        cid,
                        attempt,
                        attempts,
                        delay,
                    )
                except (TimedOut, NetworkError) as e:
                    last_err = e
                    delay = min(0.8 * (2 ** (attempt - 1)), 8.0)
                    self._cooldown(cid, delay)
                    logger.warning(
                        "[telegram] %s 网络失败，重试 chat_id=%s attempt=%d/%d delay=%.1fs err=%s",
                        label,
                        cid,
                        attempt,
                        attempts,
                        delay,
                        e,
                    )
                if attempt >= attempts:
                    break
                await self._sleep_until_ready(cid)
            if last_err is not None:
                raise last_err
            raise RuntimeError(f"{label} failed without exception")

    async def _run_typing(self, chat_id: int, *, label: str, action: Callable[[], Awaitable[_T]]) -> _T:
        lock = self._typing_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            now = asyncio.get_running_loop().time()
            wait_s = self._next_typing_at.get(chat_id, 0.0) - now
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            try:
                result = await action()
                self._next_typing_at[chat_id] = now + self._typing_interval_s
                return result
            except RetryAfter as e:
                delay = float(getattr(e, "retry_after", 1.0) or 1.0) + self._retry_padding_s
                self._next_typing_at[chat_id] = now + delay
                raise

    async def _wait_for_chat_slot(self, chat_id: int) -> None:
        now = asyncio.get_running_loop().time()
        wait_s = self._next_chat_at.get(chat_id, 0.0) - now
        if wait_s > 0:
            await asyncio.sleep(wait_s)

    async def _run_with_global_slot(self, action: Callable[[], Awaitable[_T]]) -> _T:
        async with self._global_lock:
            now = asyncio.get_running_loop().time()
            wait_s = self._next_global_at - now
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            try:
                return await action()
            finally:
                self._next_global_at = now + self._global_interval_s

    async def _sleep_until_ready(self, chat_id: int) -> None:
        now = asyncio.get_running_loop().time()
        wait_s = self._next_chat_at.get(chat_id, 0.0) - now
        if wait_s > 0:
            await asyncio.sleep(wait_s)

    def _mark_used(self, chat_id: int, kind: str) -> None:
        now = asyncio.get_running_loop().time()
        self._next_chat_at[chat_id] = now + self._interval(kind)

    def _cooldown(self, chat_id: int, delay: float) -> None:
        now = asyncio.get_running_loop().time()
        self._next_chat_at[chat_id] = max(self._next_chat_at.get(chat_id, 0.0), now + delay)
        self._next_global_at = max(self._next_global_at, now + self._global_interval_s)

    def _interval(self, kind: str) -> float:
        if kind == "edit":
            return self._edit_interval_s
        if kind == "typing":
            return self._typing_interval_s
        return self._send_interval_s


# ═══════════════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════════════


async def _run_outbound(
    limiter: TelegramOutboundLimiter | None,
    chat_id: int,
    *,
    kind: str,
    label: str,
    action: Callable[[], Awaitable[_T]],
) -> _T:
    if limiter is not None:
        return await limiter.run(chat_id, kind=kind, label=label, action=action)
    return await action()


def _split_text(text: str, limit: int) -> list[str]:
    chunks, current = [], []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if current_len + len(line) > limit and current:
            chunks.append("".join(current))
            current, current_len = [], 0
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def _strip_chunk(
    text: str,
    entities,
) -> tuple[str, Any]:
    leading = len(text) - len(text.lstrip("\n"))
    trailing = len(text) - len(text.rstrip("\n"))
    if leading == 0 and trailing == 0:
        return text, entities
    end = len(text) - trailing if trailing else len(text)
    stripped = text[leading:end]
    if not stripped:
        return "", []
    return stripped, entities


def _utf16_cut(text: str, max_utf16: int) -> int:
    utf16_count = 0
    for i, ch in enumerate(text):
        utf16_count += 2 if ord(ch) > 0xFFFF else 1
        if utf16_count > max_utf16:
            return i
    return len(text)


# ═══════════════════════════════════════════════════════════════════════
#  表格预处理 — 把 Markdown 表格转为列表，保留链接等富文本
# ═══════════════════════════════════════════════════════════════════════

# 匹配 Markdown 表格行：至少两个 | 分隔
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
# 匹配分隔行：|---|---| 或 | :---: | --- |
_TABLE_SEP_RE = re.compile(r"^\|[\s:\-]+\|[\s:\-|]*$")


def _flatten_tables(text: str) -> str:
    """将 Markdown 表格转为列表，保留链接等内联格式。

    telegramify_markdown 会把整个表格渲染为 ``pre`` 代码块，
    导致其中的链接、加粗等富文本全部丢失。
    转为列表后 telegramify_markdown 能正确生成 text_link 等实体。

    示例::

        | Date | Article | Link |
        |------|---------|------|
        | 6/3  | Title   | [Read](url) |

    转为::

        - **Date** | Article | Link
        - **6/3** | Title | [Read](url)
    """
    lines = text.split("\n")
    result: list[str] = []
    i = 0

    while i < len(lines):
        # 尝试识别表格块：表头行 + 分隔行 + 至少一行数据
        if _is_table_header(lines, i):
            header_cells = _parse_table_row(lines[i])
            # 跳过分隔行
            data_start = i + 2
            # 收集数据行
            data_rows: list[list[str]] = []
            j = data_start
            while j < len(lines) and _TABLE_ROW_RE.match(lines[j].strip()):
                data_rows.append(_parse_table_row(lines[j]))
                j += 1

            if data_rows:
                # 转为列表格式
                for row in data_rows:
                    # 找到有链接的列索引（优先展示）
                    parts = [c.strip() for c in row if c.strip()]
                    if header_cells:
                        # 给第一列加粗（通常是日期/名称）
                        bold_part = parts[0] if parts else ""
                        rest = " - ".join(parts[1:]) if len(parts) > 1 else ""
                        if rest:
                            result.append(f"- **{bold_part}** {rest}")
                        else:
                            result.append(f"- **{bold_part}**")
                    else:
                        result.append("- " + " | ".join(parts))
                i = j
                continue

        result.append(lines[i])
        i += 1

    return "\n".join(result)


def _is_table_header(lines: list[str], idx: int) -> bool:
    """检查 idx 位置是否是表格的表头行（需要紧跟分隔行）。"""
    if idx + 1 >= len(lines):
        return False
    current = lines[idx].strip()
    next_line = lines[idx + 1].strip()
    return bool(_TABLE_ROW_RE.match(current) and _TABLE_SEP_RE.match(next_line))


def _parse_table_row(line: str) -> list[str]:
    """解析 ``| a | b | c |`` 为 ``['a', 'b', 'c']``。"""
    stripped = line.strip()
    # 去掉首尾 |
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


# ═══════════════════════════════════════════════════════════════════════
#  最终消息发送
# ═══════════════════════════════════════════════════════════════════════


async def send_markdown(
    bot: Bot,
    chat_id: int | str,
    text: str,
    limiter: TelegramOutboundLimiter | None = None,
) -> None:
    """发送 Markdown 文本（最终消息）。"""
    cid = int(chat_id)
    # 预处理：表格→列表，避免 telegramify_markdown 把表格渲染为 pre 丢失链接
    text = _flatten_tables(text)
    try:
        from telegramify_markdown.converter import convert_with_segments
        from telegramify_markdown.entity import split_entities

        rendered_text, entities, _segments = convert_with_segments(text)
        chunks = split_entities(rendered_text, entities, 4090)
    except Exception as e:
        logger.warning("[telegram] Markdown 转换失败，降级纯文本: %s", e)
        for chunk in _split_text(text, 4090):
            await _run_outbound(
                limiter,
                cid,
                kind="send",
                label="send_message(plain)",
                action=lambda c=chunk: bot.send_message(chat_id=cid, text=c),
            )
        return
    for chunk_text, chunk_entities in chunks:
        chunk_text, chunk_entities = _strip_chunk(chunk_text, chunk_entities)
        if not chunk_text:
            continue
        await _run_outbound(
            limiter,
            cid,
            kind="send",
            label="send_message(markdown)",
            action=lambda t=chunk_text, e=chunk_entities: bot.send_message(
                chat_id=cid,
                text=t,
                entities=[entity.to_dict() for entity in e] if e else None,
            ),
        )


async def send_thinking_block(
    bot: Bot,
    chat_id: int | str,
    thinking: str,
    limiter: TelegramOutboundLimiter | None = None,
) -> None:
    """发送思考过程（expandable_blockquote）。"""
    cid = int(chat_id)
    header = "💭 思考过程\n\n"
    max_utf16 = 4080
    header_utf16 = len(header.encode("utf-16-le")) // 2

    chunks = _split_thinking(thinking, max_utf16 - header_utf16)
    for i, chunk in enumerate(chunks):
        text = (header if i == 0 else "") + chunk
        utf16_len = len(text.encode("utf-16-le")) // 2
        entity = TgEntity(type="expandable_blockquote", offset=0, length=utf16_len)
        try:
            await _run_outbound(
                limiter,
                cid,
                kind="send",
                label="send_message(thinking_block)",
                action=lambda text=text, entity=entity: bot.send_message(
                    chat_id=cid,
                    text=text,
                    entities=[entity],
                ),
            )
        except Exception as e:
            logger.warning("[telegram] thinking block 发送失败: %s", e)
            return


def _split_thinking(text: str, max_utf16: int) -> list[str]:
    if len(text.encode("utf-16-le")) // 2 <= max_utf16:
        return [text]
    chunks: list[str] = []
    current_lines: list[str] = []
    current_utf16 = 0
    for line in text.splitlines(keepends=True):
        line_utf16 = len(line.encode("utf-16-le")) // 2
        if current_utf16 + line_utf16 > max_utf16 and current_lines:
            chunks.append("".join(current_lines))
            current_lines, current_utf16 = [], 0
        while line_utf16 > max_utf16:
            cut = _utf16_cut(line, max_utf16)
            chunks.append(line[:cut])
            line = line[cut:]
            line_utf16 = len(line.encode("utf-16-le")) // 2
        current_lines.append(line)
        current_utf16 += line_utf16
    if current_lines:
        chunks.append("".join(current_lines))
    return chunks
