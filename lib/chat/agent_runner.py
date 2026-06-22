from __future__ import annotations

import asyncio
import time

from core.agent import AgentResult, get_agent_generation, run_agent
from core.db import get_async_session_maker
from lib.agent.message_builder import build_messages
from lib.agent.system_prompt_builder import detect_and_build, get_system_prompt_fingerprint
from lib.agent.types import AgentContext
from lib.bus.event_bus import (
    EventBus,
    StreamDeltaReady,
    SubagentProgress,
    TurnStarted,
)
from lib.bus.queue import InboundMessage, MessageBus, OutboundMessage
from lib.chat.persistence import ensure_conversation, persist_turn, save_user_message
from lib.metrics import record
from lib.session import get_session_manager
from shared.logging import bind_chat_context, get_logger, unbind_chat_context
from shared.path_utils import find_project_root

logger = get_logger(__name__)

# 限制携带的完整历史消息数（4 轮 user + assistant），防止 input 线性增长拖低 cache 率。
_MEMORY_WINDOW = 8

_runner: AgentRunner | None = None


def get_agent_runner() -> AgentRunner | None:
    """获取当前全局 AgentRunner（主要用于 Channel 命令如 /stop）。"""
    return _runner


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
        self._active_turns: dict[str, asyncio.Task] = {}

    def start(self) -> None:
        """启动后台任务"""
        global _runner
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        _runner = self
        logger.info("AgentRunner started")

    async def stop(self) -> None:
        """停止后台任务"""
        global _runner
        self._running = False
        for task in list(self._active_turns.values()):
            if not task.done():
                task.cancel()
        self._active_turns.clear()
        if self._task:
            await self._bus.publish_inbound(None)  # type: ignore[arg-type]
            self._task.cancel()
            import contextlib

            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if _runner is self:
            _runner = None
        logger.info("AgentRunner stopped")

    def cancel_turn(self, session_key: str) -> bool:
        """取消指定 session 正在运行的 turn（如 Telegram /stop）。"""
        task = self._active_turns.get(session_key)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

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

        current_task = asyncio.current_task()
        if current_task is not None:
            self._active_turns[session_key] = current_task

        # ── Phase 0: TurnStarted ──
        # turn 计时起点：从这里到 finally 块覆盖所有路径（成功/取消/异常）
        turn_started = time.perf_counter()
        turn_outcome = "error"  # 默认值，正常完成会覆盖
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
                    session_mgr.prune_for_context_cache(session)

                    # ── Phase 2: BeforeReasoning ──
                    # 先取 history（不含当前消息），再构建 context_frame
                    history = get_history_since_consolidated(session, _MEMORY_WINDOW)

                    # conversation 级 system prompt 冻结：若该会话已有持久化快照，
                    # 直接复用，不再重新构建，避免记忆写入后刷新 prefix cache。
                    snapshot = conv.context_snapshot or {}
                    cached_system_prompt = snapshot.get("system_prompt")
                    if cached_system_prompt:
                        logger.debug(
                            "using frozen system prompt snapshot",
                            conversation_id=conv.conversation_id,
                        )

                    _skill_names, system_prompt, skills_frame = await detect_and_build(
                        user_input,
                        user_id,
                        conv.conversation_id,
                        cached_system_prompt=cached_system_prompt,
                    )

                    # 首次进入该 conversation 时冻结 system prompt，持久化到
                    # context_snapshot，供后续轮次（以及跨进程重启）复用。
                    if not cached_system_prompt:
                        conv.context_snapshot = {
                            "system_prompt": system_prompt,
                            "fingerprint": await get_system_prompt_fingerprint(user_id),
                        }
                        await db.flush()

                    context_frame = await _build_context_frame(conv, user_id, user_input, session_key, skills_frame)
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
                    # 存入 llm_context_frame，get_history() 重构时原样回放，保证跨轮 prefix cache
                    session.add_message(
                        "user",
                        user_input,
                        media=media_paths,
                        llm_context_frame=context_frame,
                    )
                    session.add_message(
                        "assistant",
                        result.content,
                        tool_chain=result.tool_chain if result.tool_chain else None,
                    )
                    session_mgr.save(session)
                    session_mgr.prune_for_context_cache(session)

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
                turn_outcome = "success"

            except asyncio.CancelledError:
                logger.info("Turn cancelled", session_key=session_key)
                turn_outcome = "cancelled"
                await db.rollback()
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="⏹ 已停止当前任务。",
                    )
                )
            except Exception:
                logger.exception("生成 AI 回复失败")
                turn_outcome = "error"
                await db.rollback()
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="生成回复失败，请稍后重试",
                    )
                )
            finally:
                # ── metrics：turn 完成（覆盖所有 outcome）──
                # finally 是唯一保证在所有路径都执行的出口
                turn_duration_ms = (time.perf_counter() - turn_started) * 1000
                try:
                    await record(
                        "turn.duration_ms",
                        turn_duration_ms,
                        labels={"channel": msg.channel, "outcome": turn_outcome},
                    )
                    await record(
                        "turn.completed",
                        1.0,
                        labels={"channel": msg.channel, "outcome": turn_outcome},
                    )
                except Exception:
                    pass  # 绝不让观测拖垮业务
                self._active_turns.pop(session_key, None)
                unbind_chat_context()


# ── Context Frame 构建 ──────────────────────────────────────────────


def get_history_since_consolidated(session, memory_window: int) -> list[dict]:
    try:
        return session.get_history(
            max_messages=memory_window,
            start_index=session.last_consolidated,
        )
    except TypeError:
        return session.get_history(max_messages=memory_window)


async def _build_context_frame(
    conv,
    user_id: str,
    user_input: str,
    session_key: str,
    skills_frame: str = "",
) -> str:
    """构建 context frame（参照 akashic-agent）。

    只包含动态内容：日期、L2 外部召回、skills、deferred hint。
    L0 + PARTNER.md 已移入 system prompt。
    L1 近期对话已移除，依赖 MEMORY.md 保持跨对话连续性。
    """
    from datetime import datetime

    from lib.memory import get_memory_manager
    from lib.tools.factory import build_deferred_tools_hint

    now = datetime.now()
    hour = now.hour
    if hour < 6:
        period = "凌晨"
    elif hour < 12:
        period = "上午"
    elif hour < 18:
        period = "下午"
    else:
        period = "晚上"
    timestamp = now.strftime(f"%Y-%m-%d {period}")
    manager = get_memory_manager()

    parts: list[str] = [f"当前时间：{timestamp}（以此时此刻为准，记忆中的时间信息可能过时）"]

    # Skills 内容
    if skills_frame.strip():
        parts.append(skills_frame)

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

    return "\n\n".join(parts)
