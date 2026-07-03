"""Telegram 入站消息处理 — 文本、文档、图片、语音/音频/视频。

职责单一：从 Telegram Update 提取消息内容，下载文件附件，
将平台无关的 InboundMessage 发布到 MessageBus。
"""

from __future__ import annotations

import contextlib
import logging

from channels.builtins.telegram.downloader import download_file
from lib.bus.queue import InboundMessage, MessageBus
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logger = logging.getLogger(__name__)


class TelegramHandlers:
    """入站消息处理器 — 将 Telegram Update 转为 InboundMessage。

    不持有 Bot 引用、不关心出站逻辑，只做「收消息 → 发 Bus」。
    """

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus

    def register(self, app: Application) -> None:
        """将所有 handler 注册到 Application。"""
        app.add_handler(CommandHandler("clear", self.on_clear))
        app.add_handler(CommandHandler("new", self.on_clear))
        app.add_handler(CommandHandler("stop", self.on_stop))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        app.add_handler(MessageHandler(filters.Document.ALL, self.on_document))
        app.add_handler(MessageHandler(filters.PHOTO, self.on_photo))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO, self.on_media))

    # ── 命令 ──

    async def on_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/clear 或 /new — 彻底清空当前会话（session store + 主 DB + 外部记忆 provider）"""
        if not update.effective_message:
            return
        chat_id = str(update.effective_chat.id)

        session_key = f"telegram:{chat_id}"
        cleared: list[str] = []

        # 1. 清理 session store（sessions.db）
        from lib.session import get_session_manager

        session_mgr = get_session_manager()
        try:
            session_mgr.delete_session(session_key, cascade=True)
            cleared.append("session store")
        except Exception:
            logger.exception("[telegram] Failed to clear session store %s", session_key)

        # 2. 清理主 DB（lumen.db Conversation + Message）
        try:
            from sqlalchemy import delete as sql_delete

            from core.db import get_async_session_maker
            from lib.chat.models import Conversation, Message

            async_session = get_async_session_maker()
            async with async_session() as db:
                await db.execute(sql_delete(Message).where(Message.conversation_id == chat_id))
                conv = await db.get(Conversation, chat_id)
                if conv:
                    await db.delete(conv)
                await db.commit()
            cleared.append("main DB")
        except Exception:
            logger.exception("[telegram] Failed to clear main DB for %s", chat_id)

        # 3. 重置外部记忆 provider session（Honcho 等）
        try:
            from lib.memory import get_memory_manager

            manager = get_memory_manager()
            await manager.on_session_switch(session_key, reset=True, old_session_id=session_key)
            cleared.append("memory providers")
        except Exception:
            logger.exception("[telegram] Failed to reset memory providers for %s", session_key)

        logger.info("[telegram] /clear completed for %s: %s", session_key, ", ".join(cleared))

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🧹 对话已清空，我们重新开始吧～",
        )

    async def on_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/stop — 尝试中断当前正在生成的回复。"""
        if not update.effective_message:
            return
        chat_id = str(update.effective_chat.id)
        session_key = f"telegram:{chat_id}"

        from lib.chat.agent_runner import get_agent_runner

        runner = get_agent_runner()
        if runner is None:
            await _reply_error(context, chat_id, "当前没有运行中的任务")
            return

        if runner.cancel_turn(session_key):
            await _reply_error(context, chat_id, "⏹ 正在停止当前任务…")
        else:
            await _reply_error(context, chat_id, "当前没有运行中的任务")

    # ── 文本 ──

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message or not update.effective_message.text:
            return

        chat_id = str(update.effective_chat.id)
        user_id = str(update.effective_user.id)
        text = update.effective_message.text

        logger.info("[telegram] Received text from %s: %s", user_id, text[:60])
        _auto_save_chat_id(chat_id)

        await self._bus.publish_inbound(
            InboundMessage(
                channel="telegram",
                sender=user_id,
                chat_id=chat_id,
                content=text,
            )
        )

    # ── 文档 ──

    async def on_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if not msg or not msg.document:
            return

        chat_id = str(update.effective_chat.id)
        user_id = str(update.effective_user.id)
        doc = msg.document
        caption = msg.caption or ""

        logger.info("[telegram] Received document from %s: %s (%s)", user_id, doc.file_name, doc.mime_type)
        _auto_save_chat_id(chat_id)

        raw = await download_file(await doc.get_file(), doc.file_name or "document")
        if not raw:
            # 下载失败：通知用户（需要 bot 引用，从 context 取）
            await _reply_error(context, chat_id, "文件下载失败，请重试")
            return

        content = caption if caption else f"[用户发送了一份文件: {doc.file_name}]"

        await self._bus.publish_inbound(
            InboundMessage(
                channel="telegram",
                sender=user_id,
                chat_id=chat_id,
                content=content,
                media=[raw],
            )
        )

    # ── 图片 ──

    async def on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if not msg or not msg.photo:
            return

        chat_id = str(update.effective_chat.id)
        user_id = str(update.effective_user.id)
        caption = msg.caption or ""

        logger.info("[telegram] Received photo from %s (%d sizes)", user_id, len(msg.photo))
        _auto_save_chat_id(chat_id)

        # 取最大尺寸（列表从小到大）
        photo = msg.photo[-1]
        raw = await download_file(
            await photo.get_file(),
            f"photo_{photo.file_unique_id}.jpg",
        )
        if not raw:
            await _reply_error(context, chat_id, "图片下载失败，请重试")
            return

        content = caption if caption else "[用户发送了一张图片]"

        await self._bus.publish_inbound(
            InboundMessage(
                channel="telegram",
                sender=user_id,
                chat_id=chat_id,
                content=content,
                media=[raw],
            )
        )

    # ── 语音 / 音频 / 视频 ──

    async def on_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if not msg:
            return

        chat_id = str(update.effective_chat.id)
        user_id = str(update.effective_user.id)
        caption = msg.caption or ""

        _auto_save_chat_id(chat_id)

        tg_file = None
        name = ""
        kind_label = ""

        if msg.voice:
            tg_file = await msg.voice.get_file()
            name = f"voice_{msg.voice.file_unique_id}.ogg"
            kind_label = "语音"
        elif msg.audio:
            tg_file = await msg.audio.get_file()
            name = msg.audio.file_name or f"audio_{msg.audio.file_unique_id}.mp3"
            kind_label = "音频"
        elif msg.video:
            tg_file = await msg.video.get_file()
            name = msg.video.file_name or f"video_{msg.video.file_unique_id}.mp4"
            kind_label = "视频"

        if not tg_file:
            return

        logger.info("[telegram] Received %s from %s: %s", kind_label, user_id, name)

        raw = await download_file(tg_file, name)
        if not raw:
            await _reply_error(context, chat_id, f"{kind_label}下载失败，请重试")
            return

        content = caption if caption else f"[用户发送了一段{kind_label}]"

        await self._bus.publish_inbound(
            InboundMessage(
                channel="telegram",
                sender=user_id,
                chat_id=chat_id,
                content=content,
                media=[raw],
            )
        )


# ═══════════════════════════════════════════════════════════════
#  辅助函数（模块级，无状态）
# ═══════════════════════════════════════════════════════════════


def _auto_save_chat_id(chat_id: str) -> None:
    """自动填充 telegram_chat_id，便于 Telegram 渠道后续主动推送使用。"""
    try:
        from core.config import load_user_config, save_user_config

        cfg = load_user_config()
        if not cfg.get("telegram_chat_id"):
            save_user_config({"telegram_chat_id": chat_id})
    except Exception:
        pass


async def _reply_error(context: ContextTypes.DEFAULT_TYPE, chat_id: str, text: str) -> None:
    """通过 context.bot 回复错误消息。"""
    with contextlib.suppress(Exception):
        await context.bot.send_message(chat_id=int(chat_id), text=text)
