"""QA Runner — 在隔离的 runtime 中对 Agent 提问并收集回答。

使用新架构：AgentRunner + MessageBus + EventBus
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from eval.dataset import LMEInstance
from eval.runtime import BenchmarkRuntime
from lib.bus.event_bus import EventBus
from lib.bus.queue import InboundMessage, MessageBus, OutboundMessage
from lib.chat.agent_runner import AgentRunner

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 180.0


async def _collect_stream(user_id: str, question: str) -> str:
    """使用 AgentRunner + MessageBus 收集 Agent 回复。"""
    bus = MessageBus()
    event_bus = EventBus()
    runner = AgentRunner(bus, event_bus)
    runner.start()

    # 收集 outbound 消息
    response_content: list[str] = []

    async def on_response(msg: OutboundMessage) -> None:
        response_content.append(msg.content)

    bus.subscribe_outbound("web", on_response)

    # 启动分发
    dispatch_task = asyncio.create_task(bus.dispatch_outbound())

    try:
        # 发布问题
        await bus.publish_inbound(
            InboundMessage(
                channel="web",
                sender=user_id,
                chat_id=f"qa-{time.time()}",
                content=question,
            )
        )

        # 等待回复（带超时）
        for _ in range(int(_DEFAULT_TIMEOUT_S * 10)):
            if response_content:
                return response_content[0]
            await asyncio.sleep(0.1)

        return ""
    finally:
        await runner.stop()
        dispatch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dispatch_task


async def run_qa_instance(
    rt: BenchmarkRuntime,
    instance: LMEInstance,
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> dict:
    """Run one QA turn and return a result dict.

    Returns:
        {
            "question_id": str,
            "question_type": str,
            "question": str,
            "gold_answer": str,
            "predicted_answer": str,
            "elapsed_s": float,
            "error": str | None,
        }
    """
    question = instance.question
    predicted = ""
    error: str | None = None
    t0 = time.monotonic()

    try:
        predicted = await asyncio.wait_for(
            _collect_stream(rt.user_id, question),
            timeout=timeout_s,
        )
    except TimeoutError:
        error = f"timeout after {timeout_s}s"
        logger.warning("QA timeout: %s", instance.question_id)
    except Exception as exc:
        error = str(exc)
        logger.exception("QA error: %s", instance.question_id)

    elapsed = time.monotonic() - t0

    return {
        "question_id": instance.question_id,
        "question_type": instance.question_type,
        "question": instance.question,
        "gold_answer": instance.answer,
        "predicted_answer": predicted,
        "elapsed_s": round(elapsed, 2),
        "error": error,
    }
