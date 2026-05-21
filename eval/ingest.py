"""Ingest — 将 haystack 对话历史写入 Lumen 记忆系统。

career-os 没有自动 consolidation 机制，因此 ingest 阶段直接调用
LumenMemory.remember() 把 haystack 内容保存为 narrative 事件。
这样 QA 阶段 Agent 只能通过记忆系统召回，不能依赖对话历史。
"""

from __future__ import annotations

import json
import logging

from eval.dataset import LMEInstance
from eval.runtime import BenchmarkRuntime
from lib.memory import get_memory

logger = logging.getLogger(__name__)

_INGEST_STATE_FILE = "ingest_state.json"


def _is_ingested(rt: BenchmarkRuntime) -> bool:
    path = rt.workspace / _INGEST_STATE_FILE
    if not path.exists():
        return False
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return bool(state.get("completed"))
    except Exception:
        return False


def _write_ingest_state(rt: BenchmarkRuntime, completed: bool, turns: int) -> None:
    path = rt.workspace / _INGEST_STATE_FILE
    path.write_text(
        json.dumps(
            {"completed": completed, "ingested_turns": turns},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


async def ingest_instance(
    rt: BenchmarkRuntime,
    instance: LMEInstance,
    *,
    force: bool = False,
) -> int:
    """将 haystack 写入记忆系统，返回总 turn 数。

    策略：
      - 每个 session 的整体对话文本作为一个 significant_moment 事件
      - 这样 FTS5 可以索引全部内容，memory_search 能命中
    """
    if not force and _is_ingested(rt):
        logger.info("skip ingest (already done): %s", instance.question_id)
        return 0

    memory = get_memory()
    total_turns = 0

    _write_ingest_state(rt, completed=False, turns=0)

    for session_idx, turns in enumerate(instance.haystack_sessions):
        lines = []
        for t in turns:
            role = "User" if t.role == "user" else "Assistant"
            lines.append(f"{role}: {t.content}")
            total_turns += 1

        session_text = "\n".join(lines)

        await memory.remember(
            user_id=rt.user_id,
            event_type="significant_moment",
            payload={
                "title": f"Session {session_idx + 1}",
                "description": session_text,
                "source": "benchmark_haystack",
            },
            source="benchmark",
        )

        logger.debug(
            "ingest session: qid=%s session=%d turns=%d",
            instance.question_id,
            session_idx,
            len(turns),
        )

    _write_ingest_state(rt, completed=True, turns=total_turns)
    logger.info(
        "ingest done: %s  sessions=%d  turns=%d",
        instance.question_id,
        len(instance.haystack_sessions),
        total_turns,
    )
    return total_turns
