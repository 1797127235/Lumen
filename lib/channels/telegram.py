from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from lib.bus.event_bus import (
    EventBus,
    StreamDeltaReady,
    ToolCallCompleted,
    ToolCallStarted,
    TurnStarted,
)
from lib.bus.queue import InboundMessage, MessageBus, OutboundMessage
from lib.channels.base import BaseChannel
from lib.channels.telegram_utils import (
    TelegramOutboundLimiter,
    send_markdown,
    send_thinking_block,
)

logger = logging.getLogger(__name__)


class TelegramChannel(BaseChannel):
    """Telegram Bot Channel — Polling 模式。

    行为对标 akashic-agent：
    - 流式阶段只内部收集，不操作 Telegram API
    - Agent 完成后一次性发送：思考过程（可选）→ 最终回复
    """

    def __init__(self, token: str, bus: MessageBus, event_bus: EventBus) -> None:
        self._token = token
        self._bus = bus
        self._event_bus = event_bus
        request = HTTPXRequest(connection_pool_size=20)
        self._app = Application.builder().token(token).request(request).build()

        self._limiter = TelegramOutboundLimiter()
        # chat_id -> 累积内容（流式阶段只内部收集）
        self._reply_buffers: dict[int, str] = {}
        self._thinking_buffers: dict[int, str] = {}
        self._polling_task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动 Telegram Polling 并订阅事件"""
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))

        self._bus.subscribe_outbound("telegram", self._on_response)

        self._event_bus.on(StreamDeltaReady, self._on_stream_delta)
        self._event_bus.on(ToolCallStarted, self._on_tool_started)
        self._event_bus.on(ToolCallCompleted, self._on_tool_completed)
        self._event_bus.on(TurnStarted, self._on_turn_started)

        await self._app.initialize()
        await self._app.start()
        # 关键：start_polling 会阻塞，必须放到后台运行
        self._polling_task = asyncio.create_task(self._app.updater.start_polling(allowed_updates=Update.ALL_TYPES))

        logger.info("TelegramChannel started")

    async def stop(self) -> None:
        """停止 Telegram Polling"""
        try:
            if self._polling_task:
                self._polling_task.cancel()
                try:
                    await self._polling_task
                except asyncio.CancelledError:
                    pass
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
    #  入站消息
    # ═══════════════════════════════════════════════════════════════

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message or not update.effective_message.text:
            return

        chat_id = str(update.effective_chat.id)
        user_id = str(update.effective_user.id)
        text = update.effective_message.text

        logger.info("[telegram] Received from %s: %s", user_id, text[:60])

        await self._bus.publish_inbound(
            InboundMessage(
                channel="telegram",
                sender=user_id,
                chat_id=chat_id,
                content=text,
            )
        )

    # ═══════════════════════════════════════════════════════════════
    #  EventBus 事件处理 — 只内部收集，不操作 Telegram
    # ═══════════════════════════════════════════════════════════════

    async def _on_turn_started(self, event: TurnStarted) -> None:
        """新轮次开始，清理上一轮 buffer"""
        if event.channel != "telegram":
            return
        chat_id = int(event.chat_id)
        self._reply_buffers.pop(chat_id, None)
        self._thinking_buffers.pop(chat_id, None)
        logger.debug("[telegram] Turn started for chat_id=%s", chat_id)

    async def _on_stream_delta(self, event: StreamDeltaReady) -> None:
        """只累积到 buffer，不发送"""
        if event.channel != "telegram":
            return
        chat_id = int(event.chat_id)
        if event.content_delta:
            self._reply_buffers[chat_id] = self._reply_buffers.get(chat_id, "") + event.content_delta
        if event.thinking_delta:
            self._thinking_buffers[chat_id] = self._thinking_buffers.get(chat_id, "") + event.thinking_delta

    async def _on_tool_started(self, event: ToolCallStarted) -> None:
        """工具调用开始 — 发送 🟡 状态消息"""
        if event.channel != "telegram":
            return
        args_preview = str(event.arguments)[:80] if event.arguments else ""
        text = f"🟡 调用 `{event.tool_name}`…"
        if args_preview:
            text += f"\n<code>{args_preview}</code>"
        await self._send_tool_status(int(event.chat_id), "tool_status(start)", text)

    async def _on_tool_completed(self, event: ToolCallCompleted) -> None:
        """工具调用完成 — 发送 ✅/❌ 状态消息"""
        if event.channel != "telegram":
            return
        status_icon = "✅" if event.status == "done" else "❌"
        result_preview = event.result_preview[:200] if event.result_preview else ""
        text = f"{status_icon} `{event.tool_name}`"
        if result_preview:
            text += f"\n<pre>{result_preview}</pre>"
        await self._send_tool_status(int(event.chat_id), "tool_status(end)", text)

    async def _send_tool_status(self, chat_id: int, label: str, text: str) -> None:
        """发送工具状态消息（统一错误处理）。"""
        try:
            await self._limiter.run(
                chat_id,
                kind="send",
                label=label,
                action=lambda: self._app.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML",
                ),
            )
        except Exception as e:
            logger.warning("[telegram] Failed to send %s: %s", label, e)

    # ═══════════════════════════════════════════════════════════════
    #  出站消息 — 一次性发送思考 + 回复
    # ═══════════════════════════════════════════════════════════════

    async def _on_response(self, msg: OutboundMessage) -> None:
        """Agent 完成，一次性发送最终内容"""
        chat_id = int(msg.chat_id)

        # 优先使用 AgentRunner 提供的最终内容，否则 fallback 到 buffer
        final_reply = msg.content or self._reply_buffers.pop(chat_id, "")
        final_thinking = msg.thinking or self._thinking_buffers.pop(chat_id, "")

        try:
            # 1. 发送思考过程（如果存在）
            if final_thinking:
                await send_thinking_block(self._app.bot, chat_id, final_thinking, self._limiter)

            # 2. 发送最终回复
            if final_reply:
                await send_markdown(self._app.bot, chat_id, final_reply, self._limiter)

        except Exception as e:
            logger.error("[telegram] Failed to send response: %s", e)
            try:
                await self.send_message(msg.chat_id, msg.content)
            except Exception as e2:
                logger.error("[telegram] Fallback send also failed: %s", e2)
