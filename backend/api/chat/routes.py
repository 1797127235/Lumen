"""对话 API + SSE 流式对话服务"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.settings import ModelSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.deps import LumenDeps
from backend.agent.event_handlers import EVENT_HANDLERS
from backend.agent.pydantic_agent import get_agent, get_agent_generation
from backend.api.chat.lock import ConversationLock, LockCapacityError
from backend.api.chat.persistence import _log_task_error, persist_turn, save_user_message
from backend.api.chat.session import ensure_conversation, load_pydantic_history
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
    last_message_at: str | None
    created_at: str


class MessageItem(BaseModel):
    message_id: str
    role: str
    content: str | None
    intent: str | None
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


# ═══════════════════════════════════════════
#  流式对话编排
# ═══════════════════════════════════════════


@dataclass
class _TurnState:
    full_content: str = ""
    usage_data: dict | None = None
    cancelled: bool = False
    new_msgs: list = field(default_factory=list)
    trace_records: list[dict] = field(default_factory=list)
    step: int = 0


async def stream_chat(
    db: AsyncSession,
    user_id: str,
    user_input: str,
    conversation_id: str | None = None,
    cancel_event: asyncio.Event | None = None,
):
    cancel_event = cancel_event or asyncio.Event()

    conv_result = await ensure_conversation(db, user_id, conversation_id, user_input)
    if isinstance(conv_result, str):
        yield {"type": "error", "message": conv_result}
        return
    conv = conv_result

    yield {"type": "token", "content": "", "conversation_id": conv.conversation_id}

    if not await save_user_message(db, conv, user_input):
        yield {"type": "error", "message": "消息保存失败，请稍后重试"}
        return

    state = _TurnState()
    try:
        async with ConversationLock(conv.conversation_id):
            await db.refresh(conv)

            agent = get_agent()
            agent_generation = get_agent_generation()
            deps = LumenDeps(
                user_id=user_id,
                db=db,
                conversation_id=conv.conversation_id,
                current_user_input=user_input,
                agent_generation=agent_generation,
            )

            history = load_pydantic_history(conv)

            async for event in agent.run_stream_events(
                user_input,
                message_history=history,
                deps=deps,
                model_settings=ModelSettings(max_tokens=4096),
            ):
                if cancel_event.is_set():
                    state.cancelled = True
                    break

                handler = EVENT_HANDLERS.get(event.event_kind)
                if handler:
                    for item in handler(event, state, {"conversation_id": conv.conversation_id}):
                        yield item

            if state.full_content:
                await persist_turn(db, conv, state, user_id, user_input, agent_generation, deps)

    except LockCapacityError:
        yield {"type": "error", "message": "服务繁忙，请稍后重试"}
        return
    except asyncio.CancelledError:
        yield {"type": "cancelled", "conversation_id": conv.conversation_id}
        return
    except Exception as exc:
        if isinstance(exc, UnexpectedModelBehavior):
            logger.warning("模型返回异常", conversation_id=conv.conversation_id, error=str(exc))
            msg = "模型未返回内容，可能触发了内容过滤，请换一种说法重试"
        else:
            logger.exception("生成 AI 回复失败", conversation_id=conv.conversation_id)
            msg = "生成回复失败，请稍后重试"
        await db.rollback()
        yield {"type": "error", "message": msg}
        return

    if state.cancelled:
        yield {"type": "cancelled", "conversation_id": conv.conversation_id}
        return

    if conv.message_count >= 30 and conv.message_count % 10 == 0:
        from backend.api.routers.summary import summarize_background

        task = asyncio.create_task(summarize_background(conv.conversation_id))
        task.add_done_callback(_log_task_error)

    yield {"type": "done", "conversation_id": conv.conversation_id, "usage": state.usage_data}
