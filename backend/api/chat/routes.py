"""对话 API 路由 — HTTP 层，业务逻辑委托到 application 层。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.application.chat_service import stream_chat
from backend.db import get_db
from backend.domain.models import Conversation, Message
from backend.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


# ═══════════════════════════════════════════
#  请求/响应模型
# ═══════════════════════════════════════════


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    user_id: str = "demo_user"


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
async def send_message(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    async def sse_stream():
        async for event in stream_chat(
            db=db,
            user_id=req.user_id,
            user_input=req.message,
            conversation_id=req.conversation_id,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


@router.get("/history", response_model=list[ConversationSummary])
async def get_chat_history(
    user_id: str = Query("demo_user"), limit: int = Query(20, le=100), db: AsyncSession = Depends(get_db)
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
    conversation_id: str, user_id: str = Query("demo_user"), db: AsyncSession = Depends(get_db)
):
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权访问该会话")

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
async def delete_conversation(
    conversation_id: str, user_id: str = Query("demo_user"), db: AsyncSession = Depends(get_db)
):
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权删除该会话")

    await db.execute(Message.__table__.delete().where(Message.conversation_id == conversation_id))
    await db.delete(conv)
    await db.commit()
    return {"deleted": True}
