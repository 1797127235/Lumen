"""QA Runner — 在隔离的 runtime 中对 Agent 提问并收集回答。"""

from __future__ import annotations

import asyncio
import logging
import time

from core.db import get_async_session_maker
from eval.dataset import LMEInstance
from eval.runtime import BenchmarkRuntime
from lib.chat.service import stream_chat

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 180.0


async def _collect_stream(db, user_id: str, question: str) -> str:
    """收集 stream_chat 的全部 token 输出。"""
    tokens: list[str] = []
    async for event in stream_chat(db, user_id, question):
        kind = event.get("type")
        if kind == "token":
            tokens.append(event.get("content", ""))
        elif kind == "done":
            break
        elif kind == "error":
            raise RuntimeError(event.get("message", "stream_chat error"))
    return "".join(tokens)


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
        async with get_async_session_maker()() as db:
            predicted = await asyncio.wait_for(
                _collect_stream(db, rt.user_id, question),
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
