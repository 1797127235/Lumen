from __future__ import annotations

import asyncio

from core.agent import AgentResult, get_agent_generation, run_agent
from core.db import get_async_session_maker
from lib.agent.message_builder import build_messages
from lib.agent.system_prompt_builder import detect_and_build_skills
from lib.agent.types import AgentContext
from lib.bus.event_bus import (
    EventBus,
    StreamDeltaReady,
    SubagentProgress,
    TurnStarted,
)
from lib.bus.queue import InboundMessage, MessageBus, OutboundMessage
from lib.chat.persistence import ensure_conversation, persist_turn, save_user_message
from lib.session import get_session_manager
from shared.logging import bind_chat_context, get_logger, unbind_chat_context
from shared.path_utils import find_project_root

logger = get_logger(__name__)


class AgentRunner:
    """后台任务：持续消费 inbound 消息，运行 Agent Loop

    参照 akashic-agent PassiveTurnPipeline：
    1. BeforeTurn（会话准备）
    2. BeforeReasoning（构建 prompt + messages）
    3. Reasoning（调用 run_agent）
    4. AfterReasoning（持久化 + 构建 outbound）
    5. AfterTurn（dispatch）
    """

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
        """处理单条消息 — Pipeline 分阶段"""
        from lib.tools._registry import get_tool_registry
        from lib.tools.factory import register_all_tools

        session_key = msg.session_key
        user_id = msg.sender
        user_input = msg.content

        # ── Phase 0: TurnStarted ──
        self._event_bus.emit(
            TurnStarted(
                channel=msg.channel,
                session_key=session_key,
                chat_id=msg.chat_id,
                content=msg.content,
            )
        )

        session_mgr = get_session_manager()

        async with get_async_session_maker()() as db:
            try:
                # ── Phase 1: BeforeTurn（会话准备）──
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

                if msg.media:
                    from lib.chat.attachment import get_attachment_service

                    att_svc = get_attachment_service()
                    attachments = await att_svc.process(conv.conversation_id, msg.media)
                    media_hint = att_svc.build_content_hint(attachments)
                    if media_hint:
                        user_input = f"{media_hint}\n\n{user_input}"

                user_msg = await save_user_message(db, conv, user_input, user_id)
                if not user_msg:
                    await self._bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content="消息保存失败，请稍后重试",
                        )
                    )
                    return

                bind_chat_context(conversation_id=conv.conversation_id, user_id=user_id)

                try:
                    from lib.memory import get_memory_manager

                    manager = get_memory_manager()
                    await manager.initialize_all(session_key, user_id=user_id)
                except Exception:
                    logger.debug("initialize_all failed", session_key=session_key)

                await db.refresh(conv)

                agent_generation = get_agent_generation()
                event_bus = self._event_bus

                def _delegate_emitter(phase: str, detail: str) -> None:
                    event_bus.emit(
                        SubagentProgress(
                            channel=msg.channel,
                            session_key=session_key,
                            chat_id=msg.chat_id,
                            phase=phase,
                            detail=detail,
                        )
                    )

                ctx = AgentContext(
                    user_id=user_id,
                    db=db,
                    conversation_id=conv.conversation_id,
                    workspace_root=find_project_root(),
                    source_platform=msg.channel,
                    progress_emitter=_delegate_emitter,
                    event_bus=self._event_bus,
                )

                registry = get_tool_registry()
                if not registry.get_registered_names():
                    register_all_tools()

                async with session_mgr._lock(session_key):
                    session = session_mgr.get_or_create(session_key)
                    media_paths = [m.path for m in msg.media] if msg.media else []
                    session.add_message("user", user_input, media=media_paths)

                    # ── Phase 2: BeforeReasoning ──
                    history = session.get_history()
                    context_frame = await _build_context_frame(conv, user_id, user_input, session_key)
                    skill_names, system_prompt = detect_and_build_skills(user_input)
                    messages = build_messages(
                        system_prompt=system_prompt,
                        history=history,
                        current_message=user_input,
                        context_frame=context_frame,
                        media=msg.media,
                    )

                    # ── Phase 3: Reasoning ──
                    result: AgentResult = await run_agent(
                        messages=messages,
                        ctx=ctx,
                    )

                    self._event_bus.emit(
                        StreamDeltaReady(
                            channel=msg.channel,
                            session_key=session_key,
                            chat_id=msg.chat_id,
                            content_delta=result.content,
                        )
                    )

                    # ── Phase 4: AfterReasoning ──
                    session.add_message(
                        "assistant",
                        result.content,
                        tool_chain=result.tool_chain if result.tool_chain else None,
                    )
                    session_mgr.save(session)

                await persist_turn(
                    db,
                    conv,
                    result.content,
                    user_id,
                    user_input,
                    agent_generation,
                    ctx,
                )

                if result.content:
                    from lib.memory import get_memory_manager

                    manager = get_memory_manager()
                    try:
                        await manager.sync_all(user_input, result.content, session_id=session_key)
                    except Exception:
                        logger.warning("sync_all failed", session_key=session_key)

                # ── Phase 5: AfterTurn ──
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=result.content,
                    )
                )

            except Exception:
                logger.exception("生成 AI 回复失败")
                await db.rollback()
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="生成回复失败，请稍后重试",
                    )
                )
            finally:
                unbind_chat_context()


# ── Context Frame 构建 ──────────────────────────────────────────────


async def _build_context_frame(
    conv,
    user_id: str,
    user_input: str,
    session_key: str,
) -> str:
    """构建 context frame（参照 akashic-agent）。

    返回字符串，直接注入为 user message。
    """
    from datetime import datetime

    from lib.memory import get_memory_manager
    from lib.memory.snapshot import build_snapshot
    from lib.tools.factory import build_deferred_tools_hint

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    manager = get_memory_manager()

    parts = [f"当前时间：{timestamp}"]

    # L0 + L1：用户画像快照
    try:
        static_ctx = await build_snapshot(user_id)
        if static_ctx.strip():
            parts.append(f"# 用户记忆\n\n{static_ctx}")
        else:
            parts.append("【用户画像为空】当用户提供信息时，调用 memory_save 或 update_profile 保存。")
    except Exception:
        logger.debug("build_snapshot failed", user_id=user_id)

    # L2：外部 provider 动态召回
    try:
        dynamic_ctx = await manager.build_context(
            user_id,
            user_input=user_input,
            session_key=session_key,
        )
        if dynamic_ctx.strip():
            parts.append(dynamic_ctx)
    except Exception:
        logger.debug("MemoryManager.build_context failed", user_id=user_id)

    # 延迟工具提示
    deferred_hint = build_deferred_tools_hint(conv.conversation_id)
    if deferred_hint:
        parts.append(deferred_hint)

    # FOCUS.md
    try:
        from lib.memory.markdown import AsyncMarkdownStore

        focus_store = AsyncMarkdownStore()
        focus_content = await focus_store.read_focus(user_id)
        if focus_content.strip():
            parts.append(f"<current-focus>\n{focus_content}\n</current-focus>")
    except Exception:
        logger.debug("FOCUS.md 读取失败", user_id=user_id)

    return "\n\n".join(parts)
