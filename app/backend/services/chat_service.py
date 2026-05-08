"""对话服务 — SSE 流式对话 + 会话管理 + Agent Trace 持久化。

摘要逻辑 → services/summary_service.py
审查逻辑 → services/review_service.py
记忆管理 → services/memory_service.py
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.agent.deps import LumenDeps
from app.backend.logging_config import get_logger
from app.backend.models.conversation import Conversation, Message

logger = get_logger(__name__)

# ── 消息历史并发保护 ──

_MAX_HISTORY_LOCKS = 128
_history_locks: dict[str, asyncio.Lock] = {}


class ConversationLock:
    """按 conversation_id 隔离的 asyncio.Lock 上下文管理器。"""

    def __init__(self, conversation_id: str, *, timeout: float = 30.0):
        self.conversation_id = conversation_id
        self.timeout = timeout
        self._lock: asyncio.Lock | None = None
        self._acquired = False

    async def __aenter__(self) -> ConversationLock:
        if len(_history_locks) >= _MAX_HISTORY_LOCKS:
            _prune_history_locks()
        if len(_history_locks) >= _MAX_HISTORY_LOCKS:
            raise LockCapacityError("历史锁数量已达上限，拒绝请求")

        self._lock = _history_locks.setdefault(self.conversation_id, asyncio.Lock())
        await asyncio.wait_for(self._lock.acquire(), timeout=self.timeout)
        self._acquired = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._acquired and self._lock:
            self._lock.release()


class LockCapacityError(Exception):
    """锁容量不足。"""


def _prune_history_locks() -> None:
    stale = [cid for cid, lock in _history_locks.items() if not lock.locked()]
    for cid in stale:
        del _history_locks[cid]


# ── 数据结构 ──


@dataclass
class _TurnState:
    """单次对话轮次的可变状态。"""

    full_content: str = ""
    usage_data: dict | None = None
    cancelled: bool = False
    new_msgs: list = field(default_factory=list)
    trace_records: list[dict] = field(default_factory=list)
    step: int = 0


# ── 事件处理器 ──


class _EventHandlers:
    """PydanticAI Agent 流事件处理器集合。"""

    @staticmethod
    def function_tool_call(event, state: _TurnState, deps: dict[str, Any]) -> list[dict]:
        state.step += 1
        args = getattr(event, "args", None) or getattr(getattr(event, "part", None), "args", None)
        args_str = _safe_json(args, 300)
        tool_name = getattr(event, "tool_name", None) or getattr(getattr(event, "part", None), "tool_name", None) or ""
        state.trace_records.append(
            {
                "step_number": state.step,
                "step_type": "tool_call",
                "tool_name": tool_name,
                "tool_args": args if isinstance(args, dict) else {"raw": str(args or "")},
                "content": args_str,
            }
        )
        return [{"type": "trace", "kind": "call", "tool": tool_name, "content": args_str}]

    @staticmethod
    def function_tool_result(event, state: _TurnState, deps: dict[str, Any]) -> list[dict]:
        state.step += 1
        content = getattr(event, "content", None)
        content = _normalize_content(content)
        content_display = _truncate(content, 500)
        tool_name = getattr(event, "tool_name", None) or getattr(getattr(event, "part", None), "tool_name", None) or ""
        state.trace_records.append(
            {
                "step_number": state.step,
                "step_type": "tool_result",
                "tool_name": tool_name,
                "tool_result": content[:5000] if isinstance(content, str) else str(content)[:5000],
                "content": content_display,
            }
        )
        return [{"type": "trace", "kind": "result", "tool": tool_name, "content": content_display}]

    @staticmethod
    def part_delta(event, state: _TurnState, deps: dict[str, Any]) -> list[dict]:
        from pydantic_ai.messages import TextPartDelta, ThinkingPartDelta

        delta = event.delta
        if isinstance(delta, TextPartDelta):
            text = delta.content_delta or ""
            state.full_content += text
            return [{"type": "token", "content": text, "conversation_id": deps["conversation_id"]}]
        elif isinstance(delta, ThinkingPartDelta):
            text = delta.content_delta or ""
            return [{"type": "thinking", "content": text, "conversation_id": deps["conversation_id"]}]
        return []

    @staticmethod
    def agent_run_result(event, state: _TurnState, deps: dict[str, Any]) -> list[dict]:
        state.new_msgs = event.result.new_messages()
        if not state.cancelled:
            try:
                u = event.result.usage()
                state.usage_data = {
                    "input": u.request_tokens or 0,
                    "output": u.response_tokens or 0,
                }
            except Exception:
                pass
        return []


_EVENT_HANDLERS: dict[str, Any] = {
    "function_tool_call": _EventHandlers.function_tool_call,
    "function_tool_result": _EventHandlers.function_tool_result,
    "part_delta": _EventHandlers.part_delta,
    "agent_run_result": _EventHandlers.agent_run_result,
}


# ── 工具函数 ──


def _safe_json(value: Any, max_len: int = 0) -> str:
    try:
        s = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value or "")
    except (TypeError, ValueError):
        s = str(value or "")
    if max_len and len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


def _normalize_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _load_pydantic_history(conv) -> list:
    from pydantic_ai import ModelMessagesTypeAdapter

    if not conv.pydantic_messages:
        return []
    try:
        return ModelMessagesTypeAdapter.validate_json(conv.pydantic_messages.encode())
    except Exception as exc:
        logger.warning(
            "消息历史反序列化失败，重置为空", error=str(exc), conversation_id=getattr(conv, "conversation_id", None)
        )
        return []


def _save_pydantic_history(conv, new_msgs: list) -> None:
    from pydantic_core import to_json

    if not new_msgs:
        return
    existing = _load_pydantic_history(conv)
    updated = (existing + new_msgs)[-30:]
    conv.pydantic_messages = to_json(updated).decode()


# ── 持久化 ──


async def _persist_turn(
    db: AsyncSession,
    conv: Conversation,
    state: _TurnState,
    user_id: str,
    user_input: str,
    agent_generation: int,
    deps: LumenDeps,
) -> bool:
    """保存一轮对话的结果（消息、历史、Trace、投影、记忆审查）。"""
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
    _save_pydantic_history(conv, state.new_msgs)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("保存 AI 回复失败 (可能为部分)", conversation_id=conv.conversation_id)
        return False

    # Agent 代际检查
    from app.backend.agent.pydantic_agent import get_agent_generation

    if get_agent_generation() != agent_generation:
        logger.warning(
            "Agent 在请求执行期间被重建，当前请求使用陈旧 key",
            conversation_id=conv.conversation_id,
            gen_start=agent_generation,
            gen_now=get_agent_generation(),
        )

    # Trace 持久化
    await _persist_traces(db, conv.conversation_id, user_id, state.trace_records)

    # 记忆投影
    if deps.pending_event_ids:
        try:
            from app.backend.memory import get_memory

            await get_memory().sync_projections(user_id, deps.pending_event_ids)
        except Exception as e:
            logger.warning("记忆投影失败", error=str(e))

    # 后台记忆审查
    if not state.cancelled and not deps.pending_event_ids:
        from app.backend.services.review_service import background_memory_review

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


async def _persist_traces(
    db: AsyncSession,
    conversation_id: str,
    user_id: str,
    trace_records: list[dict],
) -> None:
    """持久化 Agent Trace 到数据库。"""
    if not trace_records:
        return
    try:
        from app.backend.models.agent_trace import AgentTrace

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


# ── 会话管理 ──


async def _ensure_conversation(
    db: AsyncSession,
    user_id: str,
    conversation_id: str | None,
    user_input: str,
) -> Conversation | str:
    """获取或创建会话。"""
    if conversation_id:
        conv = await db.get(Conversation, conversation_id)
        if conv and conv.user_id != user_id:
            return "无权访问该会话"
        if not conv:
            try:
                conv = Conversation(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    title=_truncate(user_input, 30),
                )
                db.add(conv)
                await db.flush()
            except Exception:
                logger.exception("创建会话失败", conversation_id=conversation_id, user_id=user_id)
                await db.rollback()
                return "创建会话失败，请稍后重试"
    else:
        conv = Conversation(
            user_id=user_id,
            title=_truncate(user_input, 30),
        )
        db.add(conv)
        await db.flush()
    return conv


async def _save_user_message(
    db: AsyncSession,
    conv: Conversation,
    user_input: str,
) -> bool:
    """保存用户消息并 commit。"""
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


# ── 后台任务回调 ──


def _log_task_error(task: asyncio.Task) -> None:
    if not task.cancelled() and (exc := task.exception()):
        logger.error("后台任务异常", exc_info=exc)


# ── 主流程 ──


async def stream_chat(
    db: AsyncSession,
    user_id: str,
    user_input: str,
    conversation_id: str | None = None,
    cancel_event: asyncio.Event | None = None,
):
    """SSE 流式对话。"""
    cancel_event = cancel_event or asyncio.Event()

    # 1. 获取或创建会话
    conv_result = await _ensure_conversation(db, user_id, conversation_id, user_input)
    if isinstance(conv_result, str):
        yield {"type": "error", "message": conv_result}
        return
    conv = conv_result

    yield {"type": "token", "content": "", "conversation_id": conv.conversation_id}

    # 2. 保存用户消息
    if not await _save_user_message(db, conv, user_input):
        yield {"type": "error", "message": "消息保存失败，请稍后重试"}
        return

    # 3. Agent 流式处理
    state = _TurnState()
    try:
        async with ConversationLock(conv.conversation_id):
            await db.refresh(conv)

            from pydantic_ai.settings import ModelSettings

            from app.backend.agent.pydantic_agent import get_agent, get_agent_generation

            agent = get_agent()
            agent_generation = get_agent_generation()
            deps = LumenDeps(
                user_id=user_id,
                db=db,
                conversation_id=conv.conversation_id,
                current_user_input=user_input,
                agent_generation=agent_generation,
            )

            history = _load_pydantic_history(conv)

            async for event in agent.run_stream_events(
                user_input,
                message_history=history,
                deps=deps,
                model_settings=ModelSettings(max_tokens=4096),
            ):
                if cancel_event.is_set():
                    state.cancelled = True
                    break

                handler = _EVENT_HANDLERS.get(event.event_kind)
                if handler:
                    for item in handler(event, state, {"conversation_id": conv.conversation_id}):
                        yield item

            # 持久化
            if state.full_content:
                await _persist_turn(db, conv, state, user_id, user_input, agent_generation, deps)

    except LockCapacityError:
        yield {"type": "error", "message": "服务繁忙，请稍后重试"}
        return
    except asyncio.CancelledError:
        yield {"type": "cancelled", "conversation_id": conv.conversation_id}
        return
    except Exception as exc:
        from pydantic_ai.exceptions import UnexpectedModelBehavior

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

    # 4. 滚动摘要（后台异步）
    if conv.message_count >= 30 and conv.message_count % 10 == 0:
        from app.backend.services.summary_service import summarize_background

        task = asyncio.create_task(summarize_background(conv.conversation_id))
        task.add_done_callback(_log_task_error)

    yield {"type": "done", "conversation_id": conv.conversation_id, "usage": state.usage_data}
