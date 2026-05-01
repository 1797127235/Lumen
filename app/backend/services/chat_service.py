"""对话服务 — SSE 流式对话 + 历史存 DB"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.agent.orchestrator import classify, build_system_prompt
from app.backend.agent.llm_router import chat_stream
from app.backend.models.conversation import Conversation, Message


async def stream_chat(
    db: AsyncSession,
    user_id: str,
    user_input: str,
    conversation_id: str | None = None,
) -> AsyncIterator[str]:
    """
    SSE 流式对话：
    1. 获取/创建会话
    2. 加载历史上下文
    3. LangGraph 意图分类
    4. 组装系统提示词 → chat_stream 真流式生成
    5. 存 DB
    """
    # 获取或创建会话
    if conversation_id:
        conv = await db.get(Conversation, conversation_id)
        if not conv:
            yield _sse_error("会话不存在")
            return
    else:
        conv = Conversation(
            user_id=user_id,
            title=user_input[:30] + "..." if len(user_input) > 30 else user_input,
        )
        db.add(conv)
        await db.flush()

    yield _sse_token("", conv.conversation_id)  # 初始事件（返回 conversation_id）

    # 加载历史消息
    history_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.conversation_id)
        .order_by(Message.created_at.desc())
        .limit(20)
    )
    recent = history_result.scalars().all()
    history_messages = [
        {"role": msg.role, "content": msg.content or ""}
        for msg in reversed(recent)
    ]

    # 1. LangGraph 意图分类
    intent, task_type = await classify(user_input)

    # 2. 组装系统提示词
    user_profile = await _load_user_profile(db, user_id)
    system = build_system_prompt(user_profile, intent)
    messages = [{"role": "system", "content": system}] + history_messages + [
        {"role": "user", "content": user_input}
    ]

    # 3. 保存用户消息（先落库，流中断不丢）
    db.add(Message(
        conversation_id=conv.conversation_id,
        role="user",
        content=user_input,
        intent=intent,
    ))
    await db.flush()

    # 4. 真流式生成
    full_content = ""
    async for token in chat_stream(task_type, messages):
        full_content += token
        yield _sse_token(token, conv.conversation_id)

    # 5. 保存 AI 回复
    db.add(Message(
        conversation_id=conv.conversation_id,
        role="assistant",
        content=full_content,
        intent=intent,
    ))

    conv.message_count = (conv.message_count or 0) + 2
    conv.last_message_at = datetime.now(timezone.utc)
    await db.flush()

    yield _sse_done(conv.conversation_id)


async def _load_user_profile(db: AsyncSession, user_id: str) -> dict | None:
    """从 DB 加载用户画像（含 nickname）"""
    from app.backend.models.user import User, UserProfile

    user_result = await db.execute(select(User).where(User.user_id == user_id))
    user = user_result.scalar_one_or_none()

    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        return None
    return {
        "nickname": user.nickname if user else None,
        "grade": profile.grade,
        "school_name": profile.school_name,
        "major": profile.major,
        "target_direction": profile.target_direction,
        "current_skills": profile.current_skills,
    }


def _sse_token(content: str, conversation_id: str) -> str:
    return f"data: {json.dumps({'type': 'token', 'content': content, 'conversation_id': conversation_id}, ensure_ascii=False)}\n\n"


def _sse_error(message: str) -> str:
    return f"data: {json.dumps({'type': 'error', 'message': message}, ensure_ascii=False)}\n\n"


def _sse_done(conversation_id: str) -> str:
    return f"data: {json.dumps({'type': 'done', 'conversation_id': conversation_id}, ensure_ascii=False)}\n\n"
