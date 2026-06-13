"""消息构建器

组装完整的 messages 列表：
  system prompt → history → context frame → current user message

context_frame 作为独立 user 消息放在 history 之后、当前消息之前。
每轮 context_frame 通过 llm_context_frame 存入 session，
get_history() 重构时原样回放，保证跨轮 prefix cache 命中。
"""

from __future__ import annotations

import json
from typing import Any

from shared.logging import get_logger

logger = get_logger(__name__)


def build_messages(
    *,
    system_prompt: str,
    history: list[dict[str, Any]],
    current_message: str,
    context_frame: str = "",
    media: list[str] | None = None,
) -> list[dict[str, Any]]:
    """构建完整的 messages 列表。

    顺序：system → history → context_frame(user) → user message
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    # 历史消息（已 sanitize 的干净历史）
    messages.extend(history)

    # Context frame（运行时注入，不持久化到 messages 但通过 llm_context_frame 存入 session）
    if context_frame.strip():
        messages.append({"role": "user", "content": context_frame})

    # 当前用户消息
    user_content = _build_user_content(current_message, media)
    messages.append({"role": "user", "content": user_content})

    # Context budget telemetry：帮助定位 cache 瓶颈
    budget = _estimate_messages_budget(messages)
    logger.debug(
        "context budget",
        messages=len(messages),
        chars=budget["chars"],
        tokens=budget["tokens"],
        history_messages=len(history),
    )

    return messages


def _estimate_messages_budget(messages: list[dict[str, Any]]) -> dict[str, int]:
    """轻量估算 messages 的字符数和 tokens（1 token ≈ 3 字符，参考 akashic-agent）。"""
    payload = json.dumps(messages, ensure_ascii=False)
    chars = len(payload)
    return {"messages": len(messages), "chars": chars, "tokens": max(1, chars // 3)}


def _build_user_content(text: str, media: list[str] | None) -> str:
    """构建用户消息内容。"""
    if not media:
        return text

    # 简单处理媒体引用
    refs: list[str] = []
    for item in media:
        value = str(item)
        if value.startswith(("http://", "https://")):
            refs.append(f"- 图片URL: {value}")
        else:
            refs.append(f"- 文件路径: {value}")

    if refs:
        return f"{text}\n\n[附加媒体]\n" + "\n".join(refs)
    return text
