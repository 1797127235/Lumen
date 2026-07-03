"""把 LongMemEval 历史对话写入 Lumen Akasha engine。"""

from __future__ import annotations

from typing import Any

from lib.memory.builtins.akasha.engine import AkashaEngine

from .dataset import LMEInstance


async def ingest_instance(
    engine: AkashaEngine,
    instance: LMEInstance,
    *,
    on_progress: Any = None,
) -> int:
    """将一个 instance 的 haystack_sessions 全部写入 Akasha。

    每个 session 独立写入，turn 按顺序递增 seq。
    """
    base_session_key = instance.session_key
    total_turns = 0
    for session_index, session in enumerate(instance.haystack_sessions):
        session_key = f"{base_session_key}:s{session_index}"
        seq = 0
        for turn_index, turn in enumerate(session):
            if turn.role not in ("user", "assistant"):
                continue

            # 按 (user, assistant) 对提交；遇到 user 先缓存，遇到 assistant 一起写
            if turn.role == "user":
                user_msg = turn.content
                user_msg_id = f"{session_key}:u:{seq}"
                # 尝试读取下一条 assistant
                assistant_msg = ""
                assistant_msg_id = f"{session_key}:a:{seq}"
                if turn_index + 1 < len(session):
                    next_turn = session[turn_index + 1]
                    if next_turn.role == "assistant":
                        assistant_msg = next_turn.content

                await engine.commit_turn(
                    session_key=session_key,
                    user_msg=user_msg,
                    assistant_msg=assistant_msg,
                    user_msg_id=user_msg_id,
                    assistant_msg_id=assistant_msg_id,
                    seq=seq,
                )
                seq += 1
                total_turns += 1

        if on_progress is not None:
            on_progress(session_index + 1, len(instance.haystack_sessions))

    # 简单统计
    return total_turns
