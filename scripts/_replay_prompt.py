"""复现 13:00 那轮 Lumen 发给 LLM 的完整输入。

不走 Agent Loop,只调 system prompt + context_frame 的构建函数,
把最终拼出来的 messages 打印出来。这样能看到 AI 到底同时收到了哪些信号。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main() -> None:
    from lib.agent.system_prompt_builder import build_skills_frame, build_system_prompt
    from lib.chat.agent_runner import _build_context_frame
    from lib.skills.loader import get_skills_loader

    user_id = "8595876131"
    session_key = "telegram:8595876131"
    user_input = "你好"

    # 1. system prompt
    system_prompt = await build_system_prompt(user_id, conversation_id="telegram:8595876131")

    # 2. skills frame
    loader = get_skills_loader()
    always = loader.get_always_skills()
    detected = loader.detect_skills(user_input)
    skill_names = list(dict.fromkeys(always + detected))
    skills_frame = build_skills_frame(skill_names)

    # 3. context frame (含 L1/L2/日期)
    # 需要一个 conv-like 对象,_build_context_frame 用到 conv.conversation_id
    class _FakeConv:
        conversation_id = "telegram:8595876131"

    context_frame = await _build_context_frame(_FakeConv(), user_id, user_input, session_key, skills_frame)

    out = []
    out.append("=" * 70)
    out.append("复现 13:00 「你好」这一轮的完整 LLM 输入")
    out.append("=" * 70)

    out.append("\n" + "─" * 70)
    out.append("【1】SYSTEM PROMPT")
    out.append("─" * 70)
    out.append(system_prompt)

    out.append("\n" + "─" * 70)
    out.append("【2】CONTEXT FRAME (注入 user message)")
    out.append("─" * 70)
    out.append(context_frame if context_frame else "(空)")

    out.append("\n" + "─" * 70)
    out.append("【3】CURRENT USER MESSAGE")
    out.append("─" * 70)
    out.append(user_input)

    result = "\n".join(out)
    Path("_prompt_replay.txt").write_text(result, encoding="utf-8")
    print(result)


asyncio.run(main())
