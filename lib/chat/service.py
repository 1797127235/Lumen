"""流式对话编排 — Agent Loop 核心。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from core.agent import LumenDeps, get_agent, get_agent_generation
from lib.chat.event_handlers import EVENT_HANDLERS
from lib.chat.persistence import _log_task_error, persist_turn, save_user_message
from lib.chat.session import ensure_conversation, load_pydantic_history
from shared.logging import bind_chat_context, get_logger, unbind_chat_context

logger = get_logger(__name__)


@dataclass
class _TurnState:
    full_content: str = ""
    usage_data: dict | None = None
    cancelled: bool = False
    new_msgs: list = field(default_factory=list)
    trace_records: list[dict] = field(default_factory=list)
    step: int = 0


async def stream_chat(
    db,
    user_id: str,
    user_input: str,
    conversation_id: str | None = None,
    cancel_event: asyncio.Event | None = None,
):
    """流式对话编排 — Agent Loop，生成 SSE 事件流。"""
    cancel_event = cancel_event or asyncio.Event()

    from lib.chat.lock import ConversationLock, LockCapacityError

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
    bind_chat_context(conversation_id=conv.conversation_id, user_id=user_id)
    try:
        async with ConversationLock(conv.conversation_id):
            await db.refresh(conv)

            agent = get_agent()
            agent_generation = get_agent_generation()
            # 绑定工具运行时工作区
            from shared.path_utils import find_project_root

            deps = LumenDeps(
                user_id=user_id,
                db=db,
                conversation_id=conv.conversation_id,
                current_user_input=user_input,
                agent_generation=agent_generation,
                workspace_root=find_project_root(),
            )

            history = load_pydantic_history(conv)

            async for event in agent.run_stream_events(
                user_input,
                message_history=history,
                deps=deps,
                model_settings=ModelSettings(max_tokens=4096),
                usage_limits=UsageLimits(
                    request_limit=8,  # 最多 8 轮模型请求（含工具调用）
                    tool_calls_limit=6,  # 最多 6 次成功工具调用
                ),
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
            logger.warning("模型返回异常", error=str(exc))
            msg = "模型未返回内容，可能触发了内容过滤，请换一种说法重试"
        else:
            logger.exception("生成 AI 回复失败")
            msg = "生成回复失败，请稍后重试"
        await db.rollback()
        yield {"type": "error", "message": msg}
        return
    finally:
        unbind_chat_context()

    if state.cancelled:
        yield {"type": "cancelled", "conversation_id": conv.conversation_id}
        return

    if conv.message_count >= 10 and conv.message_count % 10 == 0:
        from lib.chat.summary import summarize_background

        task = asyncio.create_task(summarize_background(conv.conversation_id))
        task.add_done_callback(_log_task_error)

    yield {"type": "done", "conversation_id": conv.conversation_id, "usage": state.usage_data}
