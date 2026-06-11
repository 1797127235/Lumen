"""对话持久化：ORM 消息保存、Trace 记录、记忆审查触发

Note: Agent 历史由 lib/session/ SessionManager 管理（独立 sessions.db）。
本模块仅负责 ORM 层（API 显示用）的持久化。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession  # pyright: ignore[reportMissingImports]

from lib.agent.types import AgentContext
from lib.chat.agent_trace import AgentTrace
from lib.chat.models import Message
from shared.logging import get_logger

logger = get_logger(__name__)


async def persist_turn(
    db: AsyncSession,
    conv,
    content: str,
    user_id: str,
    user_input: str,
    agent_generation: int,
    ctx: AgentContext,
) -> bool:
    """持久化一轮对话到 ORM（API 显示层）。Agent 历史由 SessionManager 管理。"""
    tokens_used = len(content) // 4 if content else 0

    db.add(
        Message(
            conversation_id=conv.conversation_id,
            role="assistant",
            content=content,
            intent="consultation",
            tokens_used=tokens_used,
        )
    )
    conv.message_count = (conv.message_count or 0) + 1
    conv.last_message_at = datetime.now(UTC)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("保存 AI 回复失败 (可能为部分)", conversation_id=conv.conversation_id)
        return False

    # 持久化 Trace
    trace_records = getattr(ctx, "trace_records", [])
    if trace_records:
        await persist_traces(db, conv.conversation_id, user_id, trace_records, getattr(ctx, "trace_sink", None))

    # 触发后台记忆审查
    if not getattr(ctx, "cancelled", False):
        from lib.memory.review_service import background_memory_review

        task = asyncio.create_task(
            background_memory_review(
                user_id=user_id,
                user_message=user_input,
                assistant_response=content,
                conversation_id=conv.conversation_id,
            )
        )
        task.add_done_callback(_log_task_error)

    # ── 情绪推断（对话结束后异步更新）──
    try:
        from core.db import get_async_session_maker
        from lib.partner.mood_inference import update_mood_state

        mood_task = asyncio.create_task(
            update_mood_state(get_async_session_maker, user_id), name=f"mood-inference-{conv.conversation_id[:8]}"
        )
        mood_task.add_done_callback(_log_task_error)
    except Exception:
        pass

    return True


async def persist_traces(
    db: AsyncSession,
    conversation_id: str,
    user_id: str,
    trace_records: list[dict],
    runtime_traces: list[dict] | None = None,
) -> None:
    """持久化 Agent Trace 记录。"""
    all_records: list[dict] = list(trace_records)
    if runtime_traces:
        all_records.extend(runtime_traces)

    if not all_records:
        return

    for record in all_records:
        try:
            db.add(
                AgentTrace(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    event_kind=record.get("event_kind", "unknown"),
                    tool_name=record.get("tool_name"),
                    tool_args=record.get("tool_args"),
                    tool_result=record.get("tool_result"),
                    duration_ms=record.get("duration_ms"),
                )
            )
        except Exception:
            logger.exception("持久化单条 trace 失败", conversation_id=conversation_id)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("持久化 trace 失败", conversation_id=conversation_id)


async def save_user_message(
    db: AsyncSession,
    conv,
    content: str,
    user_id: str,
) -> bool:
    """保存用户消息到 ORM（API 显示层）。"""
    db.add(
        Message(
            conversation_id=conv.conversation_id,
            role="user",
            content=content,
            intent="consultation",
        )
    )
    conv.message_count = (conv.message_count or 0) + 1
    conv.last_message_at = datetime.now(UTC)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("保存用户消息失败", conversation_id=conv.conversation_id)
        return False

    return True


def _log_task_error(task: asyncio.Task) -> None:
    """记录后台任务错误。"""
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("后台任务失败")


async def ensure_conversation(db, user_id: str, conversation_id: str | None, user_input: str):
    """确保会话存在。返回 Conversation 实例或错误信息字符串"""
    from lib.chat.models import Conversation

    if conversation_id:
        conv = await db.get(Conversation, conversation_id)
        if conv and conv.user_id != user_id:
            return "无权访问该会话"
        if not conv:
            try:
                title = user_input[:27] + "..." if len(user_input) > 30 else user_input
                conv = Conversation(conversation_id=conversation_id, user_id=user_id, title=title)
                db.add(conv)
                await db.flush()
            except Exception:
                logger.exception("创建会话失败", conversation_id=conversation_id, user_id=user_id)
                await db.rollback()
                return "创建会话失败，请稍后重试"
    else:
        title = user_input[:27] + "..." if len(user_input) > 30 else user_input
        conv = Conversation(user_id=user_id, title=title)
        db.add(conv)
        await db.flush()
    return conv
