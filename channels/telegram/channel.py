"""Telegram Bot Channel — Polling 模式。

职责：Channel 生命周期编排（start/stop）、EventBus 订阅、
流式缓冲、typing 指示器、出站消息发送。
入站消息处理委托给 handlers.py，出站格式化委托给 telegram_utils.py。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

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
    TurnStarted,
)
from lib.bus.queue import MessageBus, OutboundMessage

logger = logging.getLogger(__name__)

# typing 指示器间隔（秒）— Telegram 要求至少每 5 秒刷新一次
_TYPING_INTERVAL = 4.0


class TelegramChannel(BaseChannel):
    """Telegram Bot Channel — Polling 模式。

    - 流式阶段只内部收集，不操作 Telegram API
    - Agent 完成后一次性发送：思考过程（可选）→ 最终回复
    - 工作期间持续发 typing 指示器
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

        await self._app.initialize()
        await self._app.start()
        self._polling_task = asyncio.create_task(self._app.updater.start_polling(allowed_updates=Update.ALL_TYPES))

        logger.info("TelegramChannel started")

    async def stop(self) -> None:
        """停止 Telegram Polling"""
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()

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
    #  出站消息 — 一次性发送思考 + 回复
    # ═══════════════════════════════════════════════════════════════

    async def _on_response(self, msg: OutboundMessage) -> None:
        chat_id = int(msg.chat_id)

        self._stop_typing(chat_id)

        final_reply = msg.content or self._reply_buffers.pop(chat_id, "")
        final_thinking = msg.thinking or self._thinking_buffers.pop(chat_id, "")

        if final_thinking:
            try:
                await send_thinking_block(self._app.bot, chat_id, final_thinking, self._limiter)
            except Exception as e:
                logger.warning("[telegram] Thinking block send failed: %s", e)

        if final_reply:
            try:
                await send_markdown(self._app.bot, chat_id, final_reply, self._limiter)
            except Exception as e:
                logger.error("[telegram] Markdown send failed, fallback to plain: %s", e)
                try:
                    await self.send_message(msg.chat_id, msg.content)
                except Exception as e2:
                    logger.error("[telegram] Fallback send also failed: %s", e2)
