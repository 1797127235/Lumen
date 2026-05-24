"""对话持久化：消息保存、Trace 记录、记忆投影触发、后台审查"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession  # pyright: ignore[reportMissingImports]

from core.agent import LumenDeps
from lib.chat.agent_trace import AgentTrace
from lib.chat.models import Message
from lib.chat.session import save_pydantic_history
from shared.logging import get_logger

logger = get_logger(__name__)


async def persist_turn(
    db: AsyncSession,
    conv,
    state,
    user_id: str,
    user_input: str,
    agent_generation: int,
    deps: LumenDeps,
    context_frame_msg=None,
) -> bool:
    """持久化一轮对话：保存 AI 回复、更新消息计数、保存历史、持久化 Trace、触发记忆投影/审查"""
    # 写入 token 用量（PydanticAI 在 agent_run_result 事件中已捕获）
    tokens_used: int | None = None
    if state.usage_data:
        inp = state.usage_data.get("input") or 0
        out = state.usage_data.get("output") or 0
        cache_read = state.usage_data.get("cache_read") or 0
        cache_write = state.usage_data.get("cache_write") or 0
        tokens_used = inp + out
        # inp = total prompt tokens (hit + miss)，cache_read = hit tokens
        hit_rate = round(cache_read / inp * 100, 1) if inp else 0
        logger.info(
            "token usage",
            input=inp,
            output=out,
            cache_read=cache_read,
            cache_write=cache_write,
            cache_hit_rate=f"{hit_rate}%",
            conversation_id=conv.conversation_id,
        )

    stored_content = state.full_content
    if state.thinking_content:
        stored_content = f"<think>\n{state.thinking_content}\n</think>\n{state.full_content}"

    db.add(
        Message(
            conversation_id=conv.conversation_id,
            role="assistant",
            content=stored_content,
            intent="consultation",
            tokens_used=tokens_used,
        )
    )
    conv.message_count = (conv.message_count or 0) + 1
    conv.last_message_at = datetime.now(UTC)
    msgs_to_save = [context_frame_msg, *state.new_msgs] if context_frame_msg is not None else state.new_msgs
    save_pydantic_history(conv, msgs_to_save, summary=conv.summary)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("保存 AI 回复失败 (可能为部分)", conversation_id=conv.conversation_id)
        return False

    from core.agent import get_agent_generation as _get_agent_generation

    if _get_agent_generation() != agent_generation:
        logger.warning(
            "Agent 在请求执行期间被重建",
            conversation_id=conv.conversation_id,
            gen_start=agent_generation,
            gen_now=_get_agent_generation(),
        )

    await persist_traces(db, conv.conversation_id, user_id, state.trace_records, deps.trace_sink)

    if deps.pending_event_ids:
        try:
            from lib.memory import get_memory

            await get_memory().sync_projections(user_id, deps.pending_event_ids)
        except Exception as e:
            logger.warning("记忆投影失败", error=str(e))

    if not state.cancelled and not deps.pending_event_ids:
        from lib.memory.review_service import background_memory_review

        task = asyncio.create_task(
            background_memory_review(
                user_id=user_id,
                user_message=user_input,
                assistant_response=state.full_content,
                conversation_id=conv.conversation_id,
            )
        )
        task.add_done_callback(_log_task_error)

    # ── 新增：情绪推断（对话结束后异步更新）──
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
    """持久化 Agent Trace 记录。

    Args:
        trace_records: PydanticAI 事件流产生的 trace（tool_call / tool_result）
        runtime_traces: 新 runtime Dispatcher 产生的内部 trace（预算拒绝、路径错误等）
    """
    all_records: list[dict] = list(trace_records)

    # 将 runtime trace 转换为 AgentTrace 兼容格式
    if runtime_traces:
        import json

        for tr in runtime_traces:
            all_records.append(
                {
                    "step_number": len(all_records) + 1,
                    "step_type": "tool_dispatch",
                    "tool_name": tr.get("tool"),
                    "tool_args": tr.get("args"),
                    "tool_result": tr.get("result") if tr.get("ok") else tr.get("error"),
                    "content": json.dumps(tr, ensure_ascii=False, default=str)[:5000],
                    "duration_ms": tr.get("duration_ms", 0),
                    "success": tr.get("ok", True),
                    "error_message": tr.get("error", ""),
                }
            )

    if not all_records:
        return

    try:
        for tr in all_records:
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
                    duration_ms=tr.get("duration_ms", 0),
                    success=tr.get("success", True),
                    error_message=tr.get("error_message", ""),
                )
            )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("Agent Trace 持久化失败", conversation_id=conversation_id)


async def save_user_message(db: AsyncSession, conv, user_input: str) -> Message | None:
    """保存用户消息"""
    msg = Message(
        conversation_id=conv.conversation_id,
        role="user",
        content=user_input,
        intent="consultation",
    )
    db.add(msg)
    conv.message_count = (conv.message_count or 0) + 1
    conv.last_message_at = datetime.now(UTC)

    # ── 新增：presence 记录（与用户消息同一 transaction）──
    try:
        from lib.partner.presence import record_user_message

        _uid = getattr(conv, "user_id", "demo_user") or "demo_user"
        await record_user_message(db, _uid)
    except Exception:
        pass  # presence 失败不阻断消息保存

    try:
        await db.commit()
        await db.refresh(msg)
        return msg
    except Exception:
        logger.exception("保存用户消息失败", conversation_id=conv.conversation_id)
        await db.rollback()
        return None


def _log_task_error(task: asyncio.Task) -> None:
    """后台任务异常日志回调"""
    if not task.cancelled() and (exc := task.exception()):
        logger.error("后台任务异常", exc_info=exc)
