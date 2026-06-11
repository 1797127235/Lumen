"""消息构建器

组装完整的 messages 列表：
  system prompt → history → context frame → current user message
"""

from __future__ import annotations

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

    顺序：system → history → context_frame → user message
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    # 历史消息（已 sanitize 的干净历史）
    messages.extend(history)

    # Context frame（运行时注入，不持久化）
    if context_frame.strip():
        messages.append({"role": "user", "content": context_frame})

    # 当前用户消息
    user_content = _build_user_content(current_message, media)
    messages.append({"role": "user", "content": user_content})

    return messages


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
