"""对话持久化：消息保存、Trace 记录、记忆投影触发、后台审查"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.deps import LumenDeps
from backend.api.chat.session import save_pydantic_history
from backend.domain.models import AgentTrace, Message
from backend.logging_config import get_logger

logger = get_logger(__name__)


async def persist_turn(
    db: AsyncSession,
    conv,
    state,
    user_id: str,
    user_input: str,
    agent_generation: int,
    deps: LumenDeps,
) -> bool:
    """持久化一轮对话：保存 AI 回复、更新消息计数、保存历史、持久化 Trace、触发记忆投影/审查"""
    db.add(
        Message(
            conversation_id=conv.conversation_id,
            role="assistant",
            content=state.full_content,
            intent="consultation",
        )
    )
    conv.message_count = (conv.message_count or 0) + 1
    conv.last_message_at = datetime.now(UTC)
    save_pydantic_history(conv, state.new_msgs)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("保存 AI 回复失败 (可能为部分)", conversation_id=conv.conversation_id)
        return False

    from backend.agent.pydantic_agent import get_agent_generation as _get_agent_generation

    if _get_agent_generation() != agent_generation:
        logger.warning(
            "Agent 在请求执行期间被重建",
            conversation_id=conv.conversation_id,
            gen_start=agent_generation,
            gen_now=_get_agent_generation(),
        )

    await persist_traces(db, conv.conversation_id, user_id, state.trace_records)

    if deps.pending_event_ids:
        try:
            from backend.memory import get_memory

            await get_memory().sync_projections(user_id, deps.pending_event_ids)
        except Exception as e:
            logger.warning("记忆投影失败", error=str(e))

    if not state.cancelled and not deps.pending_event_ids:
        from backend.api.routers.review import background_memory_review

        task = asyncio.create_task(
            background_memory_review(
                user_id=user_id,
                user_message=user_input,
                assistant_response=state.full_content,
                conversation_id=conv.conversation_id,
            )
        )
        task.add_done_callback(_log_task_error)

    return True


async def persist_traces(db: AsyncSession, conversation_id: str, user_id: str, trace_records: list[dict]) -> None:
    """持久化 Agent Trace 记录"""
    if not trace_records:
        return
    try:
        for tr in trace_records:
            db.add(
                AgentTrace(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    step_number=tr["step_number"],
                    step_type=tr["step_type"],
                    tool_name=tr.get("tool_name"),
                    tool_args=tr.get("tool_args"),
                    tool_result=tr.get("tool_result"),
                    content=tr["content"][:5000],
                )
            )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("Agent Trace 持久化失败", conversation_id=conversation_id)


async def save_user_message(db: AsyncSession, conv, user_input: str) -> bool:
    """保存用户消息"""
    db.add(
        Message(
            conversation_id=conv.conversation_id,
            role="user",
            content=user_input,
            intent="consultation",
        )
    )
    conv.message_count = (conv.message_count or 0) + 1
    conv.last_message_at = datetime.now(UTC)
    try:
        await db.commit()
        return True
    except Exception:
        logger.exception("保存用户消息失败", conversation_id=conv.conversation_id)
        await db.rollback()
        return False


def _log_task_error(task: asyncio.Task) -> None:
    """后台任务异常日志回调"""
    if not task.cancelled() and (exc := task.exception()):
        logger.error("后台任务异常", exc_info=exc)
