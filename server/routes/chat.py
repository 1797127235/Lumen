"""对话 API 路由 — HTTP 层，业务逻辑委托到 application 层。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request  # pyright: ignore[reportMissingImports]
from fastapi.responses import StreamingResponse  # pyright: ignore[reportMissingImports]
from pydantic import BaseModel
from sqlalchemy import func, select  # pyright: ignore[reportMissingImports]
from sqlalchemy.ext.asyncio import AsyncSession  # pyright: ignore[reportMissingImports]

from channels.web.formatters import SSEFormatter
from core.db import get_db
from lib.chat.models import Conversation, Message
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


# ═══════════════════════════════════════════
#  请求/响应模型
# ═══════════════════════════════════════════


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    user_id: str = "me"
    attachments: list[str] = []


class ConversationUpdate(BaseModel):
    title: str | None = None
    is_pinned: bool | None = None
    user_id: str = "me"


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str | None
    message_count: int
    total_tokens: int = 0
    last_message_at: str | None
    created_at: str


class MessageItem(BaseModel):
    message_id: str
    role: str
    content: str | None
    intent: str | None
    tokens_used: int | None = None
    created_at: str


# ═══════════════════════════════════════════
#  HTTP 路由
# ═══════════════════════════════════════════


@router.post("")
async def send_message(req: ChatRequest, request: Request):
    """使用 WebChannel 处理 SSE 流式对话"""

    # 从 app.state 获取 web_channel
    web_channel = request.app.state.web_channel

    async def sse_stream():
        formatter = SSEFormatter()
        try:
            async for event in web_channel.handle_request(
                user_id=req.user_id,
                conversation_id=req.conversation_id,
                message=req.message,
            ):
                yield event
        except asyncio.CancelledError:
            logger.debug("SSE client disconnected", conversation_id=req.conversation_id)
            return
        except Exception as exc:
            logger.warning("SSE stream error", error=str(exc), conversation_id=req.conversation_id)
            yield formatter.format_error("连接中断")

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


@router.get("/history", response_model=list[ConversationSummary])
async def get_chat_history(
    user_id: str = Query("me"), limit: int = Query(20, le=100), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.last_message_at.desc())
        .limit(limit)
    )
    conversations = result.scalars().all()
    conv_ids = [c.conversation_id for c in conversations]

    # 批量查询每个对话的 token 总量
    token_map: dict[str, int] = {}
    if conv_ids:
        token_q = (
            select(Message.conversation_id, func.sum(Message.tokens_used))
            .where(
                Message.conversation_id.in_(conv_ids),
                Message.tokens_used.isnot(None),
            )
            .group_by(Message.conversation_id)
        )
        token_result = await db.execute(token_q)
        for row in token_result:
            token_map[str(row[0])] = row[1] or 0

    return [
        ConversationSummary(
            conversation_id=c.conversation_id,
            title=c.title,
            message_count=c.message_count,
            total_tokens=token_map.get(c.conversation_id, 0),
            last_message_at=c.last_message_at.isoformat() if c.last_message_at else None,
            created_at=c.created_at.isoformat(),
        )
        for c in conversations
    ]


@router.get("/{conversation_id}", response_model=list[MessageItem])
async def get_conversation_messages(
    conversation_id: str, user_id: str = Query("me"), db: AsyncSession = Depends(get_db)
):
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 单用户产品:不做 conv.user_id != user_id 权限校验(威胁不存在,且曾阻断主动送达)。

    result = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc())
    )
    messages = result.scalars().all()
    return [
        MessageItem(
            message_id=m.message_id,
            role=m.role,
            content=m.content,
            intent=m.intent,
            tokens_used=m.tokens_used,
            created_at=m.created_at.isoformat(),
        )
        for m in messages
    ]


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str, user_id: str = Query("me"), db: AsyncSession = Depends(get_db)):
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 单用户产品:不做 conv.user_id != user_id 权限校验(威胁不存在)。

    await db.execute(Message.__table__.delete().where(Message.conversation_id == conversation_id))
    await db.delete(conv)
    await db.commit()

    # 同步清理 session store（sessions.db），否则旧消息会继续被 get_history 加载
    from lib.session import get_session_manager

    session_mgr = get_session_manager()
    for prefix in ("web", "telegram"):
        sk = f"{prefix}:{conversation_id}"
        try:
            session_mgr.invalidate(sk)
            session_mgr.delete_session(sk, cascade=True)
        except Exception:
            pass

    # 清理该会话的附件副本
    from lib.chat.session_files import cleanup_session_files

    await cleanup_session_files(conversation_id)

    return {"deleted": True}


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    req: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
):
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 单用户产品:不做 conv.user_id != req.user_id 权限校验(威胁不存在)。
    if req.title is not None:
        conv.title = req.title
    if req.is_pinned is not None:
        conv.is_pinned = req.is_pinned
    await db.commit()
    return {"ok": True}
