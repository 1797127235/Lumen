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
    thinking_content: str = ""
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
    attachments: list | None = None,
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

    user_msg = await save_user_message(db, conv, user_input)
    if not user_msg:
        yield {"type": "error", "message": "消息保存失败，请稍后重试"}
        return

    # ── 附件处理：复制到 session-files，注入标记 ──
    from pydantic_ai.messages import BinaryContent

    from lib.chat.session_files import _copy_attachments, is_image

    original_input = user_input  # 保存原始输入，用于记忆搜索（避免 [attached_file] 标记进入 FTS5）
    copy_paths = await _copy_attachments(str(conv.conversation_id), attachments or [])

    image_paths = [p for p in copy_paths if is_image(p)]
    text_paths = [p for p in copy_paths if not is_image(p)]

    if text_paths:
        markers = "\n".join(f"[attached_file: {p}]" for p in text_paths)
        user_input = f"{user_input}\n\n{markers}"

    image_parts = []
    for img_path in image_paths:
        try:
            image_parts.append(BinaryContent.from_path(img_path))
        except Exception:
            logger.warning("图片读取失败", path=img_path)

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
            history_with_frame = await _inject_context_frame(history, conv, user_id, original_input)
            context_frame_msg = history_with_frame[-1] if history_with_frame else None

            # 确保工具已注册（首次调用或 Agent 重建后）
            from lib.tools._registry import get_tool_registry
            from lib.tools.factory import register_all_tools

            registry = get_tool_registry()
            if not registry.get_registered_names():
                register_all_tools()

            user_prompt = [user_input, *image_parts] if image_parts else user_input
            async for event in agent.run_stream_events(
                user_prompt,
                message_history=history_with_frame,
                deps=deps,
                model_settings=ModelSettings(max_tokens=4096),
                usage_limits=UsageLimits(
                    request_limit=12,  # 最多 12 轮模型请求（含工具调用）
                    tool_calls_limit=10,  # 最多 10 次成功工具调用
                ),
            ):
                if cancel_event.is_set():
                    state.cancelled = True
                    break

                handler = EVENT_HANDLERS.get(event.event_kind)
                if handler:
                    for item in handler(event, state, {"conversation_id": conv.conversation_id}):
                        yield item

            # 收集本轮调用的工具，更新预加载缓存（LRU）
            tool_names_used = {
                r["tool_name"] for r in state.trace_records if r.get("step_type") == "tool_call" and r.get("tool_name")
            }
            if tool_names_used:
                from lib.tools._discovery import get_tool_discovery_state

                discovery = get_tool_discovery_state()
                discovery.update(
                    conv.conversation_id,
                    list(tool_names_used),
                    registry.get_always_on_names(),
                )

            if state.full_content:
                await persist_turn(
                    db,
                    conv,
                    state,
                    user_id,
                    original_input,
                    agent_generation,
                    deps,
                    context_frame_msg=context_frame_msg,
                )

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


async def _inject_context_frame(
    history: list,
    conv,
    user_id: str,
    user_input: str,
) -> list:
    """构建 context frame 并注入为 history 末尾的 user message。

    这样 system prompt 保持完全静态，KV cache prefix 不因记忆或时间戳变化而失效。
    历史消息（system → ... → last_assistant）可持续命中 cache；
    只有 context_frame + 当前 user_input 是每次请求的新内容。

    注意：frame_msg 仅作为临时 message_history 传入当前轮对话，
    不进入持久化数据库，因此不会在历史记录中累积。
    """
    from datetime import datetime

    from pydantic_ai.messages import ModelRequest, UserPromptPart  # pyright: ignore[reportMissingImports]

    from lib.memory import get_memory

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    memory_instance = get_memory()
    try:
        context = await memory_instance.build_context(
            user_id,
            user_input=user_input,
            conversation_summary=conv.summary if conv.summary else None,
        )
    except Exception:
        context = ""

    parts = [f"当前时间：{timestamp}"]
    if context.strip():
        parts.append(f"# 用户记忆\n\n{context}")
    else:
        parts.append("【用户画像为空】当用户提供信息时，调用 memory_save 或 update_profile 保存。")

    # 注入 deferred 工具目录（动态工具发现）
    from lib.tools.factory import build_deferred_tools_hint

    deferred_hint = build_deferred_tools_hint(conv.conversation_id)
    if deferred_hint:
        parts.append(deferred_hint)

    frame_content = "\n\n".join(parts)
    frame_msg = ModelRequest(parts=[UserPromptPart(content=frame_content)])  # type: ignore[call-arg]
    return [*history, frame_msg]
