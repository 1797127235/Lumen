"""对话 WebSocket — 支持中途取消的流式对话"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.backend.db.base import get_async_session_maker
from app.backend.logging_config import get_logger
from app.backend.services.chat_service import stream_chat_ws

logger = get_logger(__name__)

router = APIRouter(tags=["chat"])


class WSMessage(BaseModel):
    type: str
    content: str = ""
    conversation_id: str | None = None
    user_id: str | None = None


async def _send_json(ws: WebSocket, data: dict[str, Any]) -> None:
    """发送 JSON 消息到客户端"""
    await ws.send_json(data)


@router.websocket("/api/chat/ws")
async def chat_ws(ws: WebSocket) -> None:
    """WebSocket 流式对话，支持 cancel"""
    await ws.accept()
    cancel_event = asyncio.Event()
    current_task: asyncio.Task | None = None

    try:
        while True:
            raw = await ws.receive_json()
            msg = WSMessage(**raw)

            if msg.type == "ping":
                await _send_json(ws, {"type": "pong"})
                continue

            if msg.type == "cancel":
                cancel_event.set()
                if current_task and not current_task.done():
                    current_task.cancel()
                # 直接发送 cancelled 消息，不等任务结束
                await _send_json(ws, {"type": "cancelled", "conversation_id": ""})
                continue

            if msg.type == "chat":
                # 重置 cancel 状态
                cancel_event.clear()

                # 取消上一轮（如果有）
                if current_task and not current_task.done():
                    current_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await current_task

                # 启动新一轮
                current_task = asyncio.create_task(_handle_chat(ws, msg, cancel_event))

    except WebSocketDisconnect:
        logger.info("WebSocket 客户端断开")
        cancel_event.set()
        if current_task and not current_task.done():
            current_task.cancel()
    except Exception:
        logger.exception("WebSocket 异常")
        cancel_event.set()
        if current_task and not current_task.done():
            current_task.cancel()


async def _handle_chat(
    ws: WebSocket,
    msg: WSMessage,
    cancel_event: asyncio.Event,
) -> None:
    """处理单轮对话"""
    user_id = msg.user_id or "demo_user"
    try:
        async with get_async_session_maker()() as db:
            async for event in stream_chat_ws(
                db=db,
                user_id=user_id,
                user_input=msg.content,
                conversation_id=msg.conversation_id,
                cancel_event=cancel_event,
            ):
                if cancel_event.is_set():
                    break
                await _send_json(ws, event)
    except asyncio.CancelledError:
        logger.info("对话任务被取消")
    except Exception:
        logger.exception("对话处理失败")
        with contextlib.suppress(Exception):
            await _send_json(ws, {"type": "error", "message": "生成回复失败，请稍后重试"})
