from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from core.agent import LumenDeps, get_agent, get_agent_generation
from core.db import get_async_session_maker
from lib.bus.event_bus import (
    EventBus,
    StreamDeltaReady,
    ToolCallCompleted,
    ToolCallStarted,
    TraceReady,
    TurnStarted,
)
from lib.bus.queue import InboundMessage, MessageBus, OutboundMessage
from lib.chat.event_handlers import EVENT_HANDLERS
from lib.chat.persistence import _log_task_error, persist_turn, save_user_message
from lib.chat.session import ensure_conversation, load_pydantic_history
from shared.logging import bind_chat_context, get_logger, unbind_chat_context
from shared.path_utils import find_project_root

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


class AgentRunner:
    """后台任务：持续消费 inbound 消息，运行 Agent Loop"""

    def __init__(self, bus: MessageBus, event_bus: EventBus) -> None:
        self._bus = bus
        self._event_bus = event_bus
        self._running = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """启动后台任务"""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("AgentRunner started")

    async def stop(self) -> None:
        """停止后台任务"""
        self._running = False
        if self._task:
            await self._bus.publish_inbound(None)  # type: ignore[arg-type]
            self._task.cancel()
            import contextlib

            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("AgentRunner stopped")

    async def _run_loop(self) -> None:
        """主循环"""
        while self._running:
            try:
                msg = await self._bus.consume_inbound()
                if msg is None:
                    break
                await self._process_message(msg)
            except Exception:
                logger.exception("AgentRunner loop error")

    async def _process_message(self, msg: InboundMessage) -> None:
        """处理单条消息 - 完整 Agent Loop"""
        from lib.chat.lock import ConversationLock, LockCapacityError
        from lib.tools._discovery import get_tool_discovery_state
        from lib.tools._registry import get_tool_registry
        from lib.tools.factory import register_all_tools

        session_key = msg.session_key
        user_id = msg.sender
        user_input = msg.content

        # 发送 TurnStarted 事件
        self._event_bus.emit(
            TurnStarted(
                channel=msg.channel,
                session_key=session_key,
                chat_id=msg.chat_id,
                content=msg.content,
            )
        )

        # 获取数据库会话
        async with get_async_session_maker()() as db:
            try:
                # 确保会话存在
                conv = await ensure_conversation(db, user_id, msg.chat_id, user_input)
                if isinstance(conv, str):
                    await self._bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=conv,
                        )
                    )
                    return

                # 保存用户消息
                user_msg = await save_user_message(db, conv, user_input)
                if not user_msg:
                    await self._bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="消息保存失败，请稍后重试",
                        )
                    )
                    return

                # 构建状态
                state = _TurnState()
                bind_chat_context(conversation_id=conv.conversation_id, user_id=user_id)

                async with ConversationLock(conv.conversation_id):
                    await db.refresh(conv)

                    # 构建 Agent
                    agent = get_agent()
                    agent_generation = get_agent_generation()
                    deps = LumenDeps(
                        user_id=user_id,
                        db=db,
                        conversation_id=conv.conversation_id,
                        current_user_input=user_input,
                        agent_generation=agent_generation,
                        workspace_root=find_project_root(),
                        source_platform=msg.channel,
                    )

                    # 确保工具已注册
                    registry = get_tool_registry()
                    if not registry.get_registered_names():
                        register_all_tools()

                    # 加载历史并注入 context frame
                    history = load_pydantic_history(conv)
                    history_with_frame = await _inject_context_frame(
                        history, conv, user_id, user_input, session_key=session_key
                    )
                    context_frame_msg = history_with_frame[-1] if history_with_frame else None

                    # 运行 Agent
                    async with agent.run_stream_events(
                        user_input,
                        message_history=history_with_frame,
                        deps=deps,
                        model_settings=ModelSettings(max_tokens=4096),
                        usage_limits=UsageLimits(
                            request_limit=12,
                            tool_calls_limit=20,
                        ),
                    ) as stream:
                        async for event in stream:
                            handler = EVENT_HANDLERS.get(event.event_kind)
                            if handler:
                                for item in handler(event, state, {"conversation_id": conv.conversation_id}):
                                    if item.get("type") == "token" and item.get("content"):
                                        self._event_bus.emit(
                                            StreamDeltaReady(
                                                channel=msg.channel,
                                                session_key=session_key,
                                                chat_id=msg.chat_id,
                                                content_delta=item["content"],
                                            )
                                        )
                                    elif item.get("type") == "thinking" and item.get("content"):
                                        self._event_bus.emit(
                                            StreamDeltaReady(
                                                channel=msg.channel,
                                                session_key=session_key,
                                                chat_id=msg.chat_id,
                                                thinking_delta=item["content"],
                                            )
                                        )
                                    elif item.get("type") == "tool_call":
                                        self._event_bus.emit(
                                            ToolCallStarted(
                                                channel=msg.channel,
                                                session_key=session_key,
                                                chat_id=msg.chat_id,
                                                call_id=item.get("call_id", ""),
                                                tool_name=item.get("tool_name", ""),
                                                arguments=item.get("arguments", {}),
                                            )
                                        )
                                    elif item.get("type") == "tool_result":
                                        self._event_bus.emit(
                                            ToolCallCompleted(
                                                channel=msg.channel,
                                                session_key=session_key,
                                                chat_id=msg.chat_id,
                                                call_id=item.get("call_id", ""),
                                                tool_name=item.get("tool_name", ""),
                                                status="done" if not item.get("is_error") else "error",
                                                result_preview=str(item.get("result", ""))[:200],
                                            )
                                        )
                                    elif item.get("type") == "trace":
                                        self._event_bus.emit(
                                            TraceReady(
                                                channel=msg.channel,
                                                session_key=session_key,
                                                chat_id=msg.chat_id,
                                                kind=item.get("kind", "call"),
                                                tool=item.get("tool", ""),
                                                content=item.get("content", ""),
                                            )
                                        )

                    # 收集本轮调用的工具，更新预加载缓存
                    tool_names_used = {
                        r["tool_name"]
                        for r in state.trace_records
                        if r.get("step_type") == "tool_call" and r.get("tool_name")
                    }
                    if tool_names_used:
                        discovery = get_tool_discovery_state()
                        discovery.update(
                            conv.conversation_id,
                            list(tool_names_used),
                            registry.get_always_on_names(),
                        )

                    # 持久化
                    if state.full_content:
                        await persist_turn(
                            db,
                            conv,
                            state,
                            user_id,
                            user_input,
                            agent_generation,
                            deps,
                            context_frame_msg=context_frame_msg,
                        )

                # 同步到外部记忆 provider
                if state.full_content:
                    from lib.memory import get_memory_manager

                    manager = get_memory_manager()
                    try:
                        await manager.sync_all(
                            user_input,
                            state.full_content,
                            session_id=session_key,
                        )
                    except Exception:
                        logger.warning("sync_all failed", session_key=session_key)

                # 发送最终回复
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=state.full_content,
                        thinking=state.thinking_content or None,
                        metadata={"usage": state.usage_data} if state.usage_data else {},
                    )
                )

                # 触发摘要（每 10 条消息）
                if conv.message_count >= 10 and conv.message_count % 10 == 0:
                    from lib.chat.summary import summarize_background

                    task = asyncio.create_task(summarize_background(conv.conversation_id))
                    task.add_done_callback(_log_task_error)

            except LockCapacityError:
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="服务繁忙，请稍后重试",
                    )
                )
            except Exception as exc:
                if isinstance(exc, UnexpectedModelBehavior):
                    logger.warning("模型返回异常", error=str(exc))
                    msg_text = "模型未返回内容，可能触发了内容过滤，请换一种说法重试"
                else:
                    logger.exception("生成 AI 回复失败")
                    msg_text = "生成回复失败，请稍后重试"
                await db.rollback()
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=msg_text,
                    )
                )
            finally:
                unbind_chat_context()


# ═══════════════════════════════════════════════════════════════
#  Context Frame 注入（从 service.py 迁移）
# ═══════════════════════════════════════════════════════════════


async def _inject_context_frame(
    history: list,
    conv,
    user_id: str,
    user_input: str,
    session_key: str = "",
) -> list:
    """构建 context frame 并注入为 history 末尾的 user message。

    Hermes-Pure 架构：
    - L0（about_you.md 冻结快照）+ L1（近期对话）由 snapshot.py 提供
    - L2（外部 provider prefetch）由 MemoryManager 提供
    """
    from datetime import datetime

    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from lib.memory import get_memory_manager
    from lib.memory.snapshot import build_snapshot
    from lib.tools.factory import build_deferred_tools_hint

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    manager = get_memory_manager()

    # L0 + L1：用户画像快照 + 近期对话
    static_ctx = ""
    try:
        static_ctx = await build_snapshot(user_id)
    except Exception:
        logger.debug("build_snapshot failed", user_id=user_id)

    # L2：外部 provider 动态召回
    dynamic_ctx = ""
    try:
        dynamic_ctx = await manager.build_context(
            user_id,
            user_input=user_input,
            session_key=session_key,
            conversation_summary=conv.summary or "",
        )
    except Exception:
        logger.debug("MemoryManager.build_context failed", user_id=user_id)

    parts = [f"当前时间：{timestamp}"]
    if static_ctx.strip():
        parts.append(f"# 用户记忆\n\n{static_ctx}")
    else:
        parts.append("【用户画像为空】当用户提供信息时，调用 memory_save 或 update_profile 保存。")

    if dynamic_ctx.strip():
        parts.append(dynamic_ctx)

    deferred_hint = build_deferred_tools_hint(conv.conversation_id)
    if deferred_hint:
        parts.append(deferred_hint)

    frame_content = "\n\n".join(parts)
    frame_msg = ModelRequest(parts=[UserPromptPart(content=frame_content)])  # type: ignore[call-arg]
    return [*history, frame_msg]
