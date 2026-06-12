"""消息构建器

组装完整的 messages 列表：
  system prompt（含冻结的 context frame）→ history → current user message

context_frame 合并进 system message，不作为独立 user 消息，
保证 [system][history] 前缀在对话内多轮复用，最大化 prefix cache 命中。
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

    顺序：system(含 context_frame) → history → user message
    context_frame 合并到 system 末尾，而非独立 user 消息，
    避免 history 前缀因 context_frame 变化而无法命中 prefix cache。
    """
    full_system = system_prompt
    if context_frame.strip():
        full_system = f"{system_prompt}\n\n---\n\n# 运行时上下文\n\n{context_frame}"

    messages: list[dict[str, Any]] = [{"role": "system", "content": full_system}]

    # 历史消息（已 sanitize 的干净历史）
    messages.extend(history)

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
