"""Akasha QA runner：召回 + LLM 回答。"""

from __future__ import annotations

import time
from typing import Any

from lib.llm.client import LLMClient
from lib.memory.builtins.akasha.engine import AkashaEngine

from .dataset import LMEInstance

_QA_SYSTEM_PROMPT = """\
You are answering a question about the user based on retrieved memory snippets.
Use only the provided memory context. If the context does not contain enough information, say "I don't remember" or "I don't know".
Do not make up facts. Be concise. Give the answer directly without phrases like "根据记忆" or "Based on the context"."""


async def run_qa_instance(
    engine: AkashaEngine,
    instance: LMEInstance,
    client: LLMClient,
    *,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """对单个 instance 执行 QA。

    1. 用 question 查询 Akasha，召回相关 turn
    2. 把召回结果 + question 发给 LLM
    3. 返回预测答案、召回 trace、耗时等
    """
    t0 = time.monotonic()
    error: str | None = None
    predicted = ""
    recall_text = ""
    recall_cards: list[dict[str, Any]] = []

    try:
        # 查询 Akasha；使用 instance 级 session key 聚合所有 haystack session
        query_session_key = instance.session_key
        result = await engine.query(query_session_key, instance.question)
        recall_text = result.text
        recall_cards = result.cards

        messages = [
            {"role": "system", "content": _QA_SYSTEM_PROMPT},
            {"role": "user", "content": _build_qa_prompt(instance.question, recall_text)},
        ]
        resp = await client.chat(messages=messages, max_tokens=512)
        predicted = resp.content or ""
    except Exception as exc:
        error = str(exc)

    elapsed = time.monotonic() - t0

    return {
        "question_id": instance.question_id,
        "question_type": instance.question_type,
        "question": instance.question,
        "gold_answer": instance.answer,
        "predicted_answer": predicted,
        "recall_text": recall_text,
        "recall_cards": recall_cards,
        "elapsed_s": round(elapsed, 2),
        "error": error,
    }


def _build_qa_prompt(question: str, recall_text: str) -> str:
    context = recall_text.strip() or "(no relevant memory retrieved)"
    return (
        f"Retrieved memory context:\n{context}\n\n"
        f"Question: {question}\n\n"
        "Answer based only on the retrieved context."
    )
