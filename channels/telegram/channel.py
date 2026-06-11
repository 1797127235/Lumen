"""Telegram Bot Channel — Polling 模式。

职责：Channel 生命周期编排（start/stop）、EventBus 订阅、
流式缓冲、typing 指示器、出站消息发送。
入站消息处理委托给 handlers.py，出站格式化委托给 telegram_utils.py。

新增：工具调用实时可视化（参照 akashic-agent）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from telegram import Update
from telegram.ext import Application
from telegram.request import HTTPXRequest

from channels.base import BaseChannel
from channels.telegram.handlers import TelegramHandlers
from channels.telegram.telegram_utils import (
    TelegramOutboundLimiter,
    send_markdown,
    send_thinking_block,
)
from lib.bus.event_bus import (
    EventBus,
    StreamDeltaReady,
    SubagentProgress,
    ToolCallCompleted,
    ToolCallStarted,
    TurnStarted,
)
from lib.bus.queue import MessageBus, OutboundMessage

logger = logging.getLogger(__name__)

# typing 指示器间隔（秒）— Telegram 要求至少每 5 秒刷新一次
_TYPING_INTERVAL = 4.0

# Live 消息编辑间隔（秒）— 避免频繁编辑触发限流
_LIVE_EDIT_INTERVAL = 1.5

# 工具调用列表最大显示行数
_MAX_TOOL_LINES = 12

# 工具预览字符限制
_TOOL_PREVIEW_LIMIT = 60


@dataclass
class _ToolLiveLine:
    """工具调用实时行"""

    call_id: str
    tool_name: str
    intent: str
    target: str
    status: str = "running"  # running | done | error


class TelegramChannel(BaseChannel):
    """Telegram Bot Channel — Polling 模式。

    - 流式阶段只内部收集，不操作 Telegram API
    - Agent 完成后一次性发送：思考过程（可选）→ 工具汇总 → 最终回复
    - 工作期间持续发 typing 指示器
    - 新增：实时显示工具调用过程（live message）
    """

    def __init__(self, token: str, bus: MessageBus, event_bus: EventBus) -> None:
        self._token = token
        self._bus = bus
        self._event_bus = event_bus

        request = HTTPXRequest(connection_pool_size=20)
        self._app = Application.builder().token(token).request(request).build()

        self._handlers = TelegramHandlers(bus)
        self._limiter = TelegramOutboundLimiter()

        # chat_id -> 累积内容（流式阶段只内部收集）
        self._reply_buffers: dict[int, str] = {}
        self._thinking_buffers: dict[int, str] = {}
        self._polling_task: asyncio.Task | None = None
        self._typing_tasks: dict[int, asyncio.Task] = {}

        # 新增：工具调用实时可视化
        # session_key -> list[_ToolLiveLine]
        self._tool_lines: dict[str, list[_ToolLiveLine]] = {}
        # session_key -> live message_id
        self._live_messages: dict[str, int] = {}
        # session_key -> live edit task
        self._live_edit_tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        """启动 Telegram Polling 并订阅事件"""
        # 注册入站消息 handler
        self._handlers.register(self._app)

        # 订阅出站
        self._bus.subscribe_outbound("telegram", self._on_response)

        # 订阅 EventBus
        self._event_bus.on(StreamDeltaReady, self._on_stream_delta)
        self._event_bus.on(TurnStarted, self._on_turn_started)
        self._event_bus.on(SubagentProgress, self._on_subagent_progress)
        # 新增：工具调用事件
        self._event_bus.on(ToolCallStarted, self._on_tool_call_started)
        self._event_bus.on(ToolCallCompleted, self._on_tool_call_completed)

        await self._app.initialize()
        await self._app.start()
        self._polling_task = asyncio.create_task(self._app.updater.start_polling(allowed_updates=Update.ALL_TYPES))

        logger.info("TelegramChannel started")

    async def stop(self) -> None:
        """停止 Telegram Polling"""
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()

        # 取消所有 live edit tasks
        for task in self._live_edit_tasks.values():
            task.cancel()
        self._live_edit_tasks.clear()

        try:
            if self._polling_task:
                self._polling_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._polling_task
            if getattr(self._app, "updater", None) and self._app.updater.running:
                await self._app.updater.stop()
            if self._app.running:
                await self._app.stop()
            await self._app.shutdown()
        except Exception as e:
            logger.warning("[telegram] 停止时出错（可能未成功初始化）: %s", e)
        finally:
            self._reply_buffers.clear()
            self._thinking_buffers.clear()
            self._tool_lines.clear()
            self._live_messages.clear()
            logger.info("TelegramChannel stopped")

    async def send_message(self, chat_id: str, content: str, **kwargs) -> None:
        """保底方法：发送纯文本"""
        await self._limiter.run(
            int(chat_id),
            kind="send",
            label="send_message(plain)",
            action=lambda: self._app.bot.send_message(
                chat_id=int(chat_id),
                text=content,
                parse_mode=None,
                disable_web_page_preview=True,
            ),
        )

    # ═══════════════════════════════════════════════════════════════
    #  Typing 指示器
    # ═══════════════════════════════════════════════════════════════

    def _start_typing(self, chat_id: int) -> None:
        if chat_id in self._typing_tasks:
            return
        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id))

    def _stop_typing(self, chat_id: int) -> None:
        task = self._typing_tasks.pop(chat_id, None)
        if task:
            task.cancel()

    async def _typing_loop(self, chat_id: int) -> None:
        from telegram.constants import ChatAction

        try:
            while True:
                try:
                    await self._app.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                except Exception as e:
                    logger.debug("[telegram] typing action failed: %s", e)
                await asyncio.sleep(_TYPING_INTERVAL)
        except asyncio.CancelledError:
            pass

    # ═══════════════════════════════════════════════════════════════
    #  EventBus 事件处理
    # ═══════════════════════════════════════════════════════════════

    async def _on_turn_started(self, event: TurnStarted) -> None:
        if event.channel != "telegram":
            return
        chat_id = int(event.chat_id)
        self._reply_buffers.pop(chat_id, None)
        self._thinking_buffers.pop(chat_id, None)
        # 清理上一轮的工具调用
        session_key = event.session_key
        self._tool_lines.pop(session_key, None)
        self._live_messages.pop(session_key, None)
        self._start_typing(chat_id)
        logger.debug("[telegram] Turn started for chat_id=%s", chat_id)

    async def _on_stream_delta(self, event: StreamDeltaReady) -> None:
        if event.channel != "telegram":
            return
        chat_id = int(event.chat_id)
        if event.content_delta:
            self._reply_buffers[chat_id] = self._reply_buffers.get(chat_id, "") + event.content_delta
        if event.thinking_delta:
            self._thinking_buffers[chat_id] = self._thinking_buffers.get(chat_id, "") + event.thinking_delta

    async def _on_subagent_progress(self, event: SubagentProgress) -> None:
        if event.channel != "telegram":
            return
        if event.phase != "step":
            return
        chat_id = int(event.chat_id)
        try:
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=f"🔍 {event.detail}",
                disable_notification=True,
            )
        except Exception as e:
            logger.debug("[telegram] SubagentProgress send failed: %s", e)

    # ═══════════════════════════════════════════════════════════════
    #  工具调用实时可视化（参照 akashic-agent）
    # ═══════════════════════════════════════════════════════════════

    async def _on_tool_call_started(self, event: ToolCallStarted) -> None:
        if event.channel != "telegram":
            return
        session_key = event.session_key
        chat_id = int(event.chat_id)

        lines = self._tool_lines.setdefault(session_key, [])
        lines.append(
            _ToolLiveLine(
                call_id=event.call_id,
                tool_name=event.tool_name,
                intent=_format_tool_intent(event.arguments),
                target=_format_tool_target(event.arguments),
            )
        )

        self._start_live_task(session_key, chat_id)

    async def _on_tool_call_completed(self, event: ToolCallCompleted) -> None:
        if event.channel != "telegram":
            return
        session_key = event.session_key
        chat_id = int(event.chat_id)

        lines = self._tool_lines.setdefault(session_key, [])
        line = next((item for item in lines if item.call_id == event.call_id), None)
        if line is None:
            line = _ToolLiveLine(
                call_id=event.call_id,
                tool_name=event.tool_name,
                intent=_format_tool_intent(event.arguments),
                target=_format_tool_target(event.arguments),
            )
            lines.append(line)

        line.status = "error" if event.status == "error" else "done"
        self._start_live_task(session_key, chat_id)

    def _start_live_task(self, session_key: str, chat_id: int) -> None:
        """启动或重置 live message 编辑任务"""
        if session_key in self._live_edit_tasks:
            return

        self._live_edit_tasks[session_key] = asyncio.create_task(self._live_edit_loop(session_key, chat_id))

    async def _live_edit_loop(self, session_key: str, chat_id: int) -> None:
        """定时编辑 live message"""
        try:
            while True:
                await self._sync_live_message(session_key, chat_id)
                await asyncio.sleep(_LIVE_EDIT_INTERVAL)
        except asyncio.CancelledError:
            pass
        finally:
            self._live_edit_tasks.pop(session_key, None)

    async def _sync_live_message(self, session_key: str, chat_id: int) -> None:
        """同步 live message 内容"""
        lines = self._tool_lines.get(session_key, [])
        if not lines:
            return

        text = _format_tool_live(lines)
        if not text:
            return

        message_id = self._live_messages.get(session_key)

        try:
            if message_id is None:
                # 首次发送
                msg = await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML",
                    disable_notification=True,
                )
                self._live_messages[session_key] = msg.message_id
            else:
                # 编辑已有消息
                await self._app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode="HTML",
                )
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.debug("[telegram] Live message sync failed: %s", e)

    async def _delete_live_message(self, session_key: str, chat_id: int) -> None:
        """删除 live message"""
        message_id = self._live_messages.pop(session_key, None)
        if message_id:
            try:
                await self._app.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception as e:
                logger.debug("[telegram] Live message delete failed: %s", e)

    # ═══════════════════════════════════════════════════════════════
    #  出站消息 — 一次性发送思考 + 工具汇总 + 回复
    # ═══════════════════════════════════════════════════════════════

    async def _on_response(self, msg: OutboundMessage) -> None:
        chat_id = int(msg.chat_id)
        session_key = f"telegram:{msg.chat_id}"

        self._stop_typing(chat_id)

        # 取消 live edit task
        task = self._live_edit_tasks.pop(session_key, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        final_reply = msg.content or self._reply_buffers.pop(chat_id, "")
        final_thinking = msg.thinking or self._thinking_buffers.pop(chat_id, "")

        # 发送思考过程
        if final_thinking:
            try:
                await send_thinking_block(self._app.bot, chat_id, final_thinking, self._limiter)
            except Exception as e:
                logger.warning("[telegram] Thinking block send failed: %s", e)

        # 发送最终工具汇总
        lines = self._tool_lines.pop(session_key, [])
        if lines:
            try:
                tool_text = _format_tool_live(lines, terminal=True)
                if tool_text:
                    await send_markdown(
                        self._app.bot,
                        chat_id,
                        f"```\n{tool_text}\n```",
                        self._limiter,
                    )
            except Exception as e:
                logger.debug("[telegram] Tool summary send failed: %s", e)

        # 删除 live message（如果存在）
        await self._delete_live_message(session_key, chat_id)

        # 发正式回复
        if final_reply:
            try:
                await send_markdown(self._app.bot, chat_id, final_reply, self._limiter)
            except Exception as e:
                logger.error("[telegram] Markdown send failed, fallback to plain: %s", e)
                try:
                    await self.send_message(msg.chat_id, msg.content)
                except Exception as e2:
                    logger.error("[telegram] Fallback send also failed: %s", e2)


# ═══════════════════════════════════════════════════════════════
#  工具调用格式化（参照 akashic-agent）
# ═══════════════════════════════════════════════════════════════


def _format_tool_live(lines: list[_ToolLiveLine], terminal: bool = False) -> str:
    """格式化工具调用列表为 Telegram HTML 文本。"""
    shown = lines[-_MAX_TOOL_LINES:]
    rows = ["工具调用"]
    hidden = len(lines) - len(shown)
    if hidden > 0:
        rows.append(f"... {hidden} more")

    for line in shown:
        status = "..."
        if line.status == "done":
            status = "✅"
        elif line.status == "error":
            status = "✗"
        target = f" {line.target}" if line.target else ""
        rows.append(
            f"{_tool_emoji(line.tool_name)} {_clip_inline(line.tool_name, 32)}: " f"{line.intent}{target} {status}"
        )

    if terminal and lines and all(line.status != "running" for line in lines):
        rows.append(f"Done · {len(lines)} tools")

    return "\n".join(rows)


def _format_tool_intent(arguments: dict) -> str:
    """提取工具调用意图（description 字段）。"""
    value = arguments.get("description")
    if value is None or value == "":
        return ""
    return _clip_inline(_stringify_tool_value(value), _TOOL_PREVIEW_LIMIT)


def _format_tool_target(arguments: dict) -> str:
    """提取工具目标参数（command/query/url/path 等）。"""
    primary_keys = (
        "cmd",
        "command",
        "query",
        "url",
        "path",
        "file",
        "text",
        "content",
        "prompt",
        "name",
    )
    for key in primary_keys:
        value = arguments.get(key)
        if value is not None and value != "":
            return f'"{_clip_inline(_stringify_tool_value(value), _TOOL_PREVIEW_LIMIT)}"'
    return ""


def _tool_emoji(tool_name: str) -> str:
    """工具名称映射 emoji。"""
    name = tool_name.lower()
    if name.startswith("mcp"):
        return "📡"
    if "search" in name:
        return "🔍"
    if "web" in name or "url" in name:
        return "🌐"
    if "file" in name or "read" in name:
        return "📄"
    if "write" in name or "save" in name:
        return "💾"
    if "shell" in name or "exec" in name:
        return "⚙"
    return "🔧"


def _clip_inline(text: str, max_len: int) -> str:
    """截断单行文本。"""
    text = text.replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _stringify_tool_value(value: object) -> str:
    """将工具参数值转为字符串。"""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    import json

    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
