"""对话 API — SSE 流式对话 + 历史查询"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.db.session import get_db
from app.backend.models.conversation import Conversation, Message
from app.backend.services.chat_service import stream_chat

router = APIRouter(prefix="/chat", tags=["chat"])

# ── 请求/响应模型 ──


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None  # 不传则新建会话
    user_id: str = "demo_user"  # MVP 阶段先写死，后续接入 JWT


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str | None
    message_count: int
    last_message_at: str | None
    created_at: str


class MessageItem(BaseModel):
    message_id: str
    role: str
    content: str | None
    intent: str | None
    created_at: str


# ── 路由 ──


@router.post("")
async def send_message(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """SSE 流式对话"""
    return StreamingResponse(
        stream_chat(db, req.user_id, req.message, req.conversation_id),
        media_type="text/event-stream",
    )


@router.get("/history", response_model=list[ConversationSummary])
async def get_chat_history(
    user_id: str = Query("demo_user"),
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_db),
):
    """获取用户对话历史列表"""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.last_message_at.desc())
        .limit(limit)
    )
    conversations = result.scalars().all()
    return [
        ConversationSummary(
            conversation_id=c.conversation_id,
            title=c.title,
            message_count=c.message_count,
            last_message_at=c.last_message_at.isoformat() if c.last_message_at else None,
            created_at=c.created_at.isoformat(),
        )
        for c in conversations
    ]


@router.get("/{conversation_id}", response_model=list[MessageItem])
async def get_conversation_messages(
    conversation_id: str,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """获取单条会话的全部消息"""
    # 校验会话归属
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
            created_at=m.created_at.isoformat(),
        )
        for m in messages
    ]


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """删除单条会话及其消息"""
    conv = await db.get(Conversation, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权删除该会话")

    await db.execute(Message.__table__.delete().where(Message.conversation_id == conversation_id))
    await db.delete(conv)
    await db.commit()
    return {"deleted": True}
