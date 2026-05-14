"""对话会话管理：创建/获取会话、消息历史序列化与压缩"""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart  # pyright: ignore[reportMissingImports]

from backend.core.logging import get_logger
from backend.modules.chat.models import Conversation

logger = get_logger(__name__)

# ── 历史压缩常量 ──────────────────────────────────────
_MAX_HISTORY_MESSAGES = 40  # 硬上限（之前是 30）
_HEAD_KEEP = 4  # 保护头部消息数（system prompt 建立上下文）
_TAIL_KEEP = 16  # 保护尾部消息数（最近对话）
_SUMMARY_MARKER = "__lumen_summary__"  # 摘要消息的标记


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _build_summary_message(summary_text: str) -> ModelMessage:
    """将摘要文本包装为一条 ModelMessage（request/system 角色），用于注入历史。"""
    # pylint: disable=unexpected-keyword-arg
    return ModelRequest(  # type: ignore[return-value]
        parts=[SystemPromptPart(content=f"[对话历史摘要] 以下是之前对话的压缩摘要，作为背景信息参考：\n{summary_text}")]
    )


def _has_summary_marker(msg: ModelMessage) -> bool:
    """检查消息是否为摘要注入消息。"""
    for part in msg.parts:
        if isinstance(part, SystemPromptPart) and _SUMMARY_MARKER in getattr(part, "content", ""):
            return True
    return False


async def ensure_conversation(db, user_id: str, conversation_id: str | None, user_input: str) -> Conversation | str:
    """确保会话存在。返回 Conversation 实例或错误信息字符串"""
    if conversation_id:
        conv = await db.get(Conversation, conversation_id)
        if conv and conv.user_id != user_id:
            return "无权访问该会话"
        if not conv:
            try:
                conv = Conversation(conversation_id=conversation_id, user_id=user_id, title=_truncate(user_input, 30))
                db.add(conv)
                await db.flush()
            except Exception:
                logger.exception("创建会话失败", conversation_id=conversation_id, user_id=user_id)
                await db.rollback()
                return "创建会话失败，请稍后重试"
    else:
        conv = Conversation(user_id=user_id, title=_truncate(user_input, 30))
        db.add(conv)
        await db.flush()
    return conv


def load_pydantic_history(conv) -> list:
    """从 Conversation.pydantic_messages 加载消息历史"""
    from pydantic_ai import ModelMessagesTypeAdapter  # pyright: ignore[reportMissingImports]

    if not conv.pydantic_messages:
        return []
    try:
        return ModelMessagesTypeAdapter.validate_json(conv.pydantic_messages.encode())
    except Exception as exc:
        logger.warning(
            "消息历史反序列化失败，重置为空",
            error=str(exc),
            conversation_id=getattr(conv, "conversation_id", None),
        )
        return []


def save_pydantic_history(conv, new_msgs: list, summary: str | None = None) -> None:
    """保存消息历史到 Conversation.pydantic_messages。

    压缩策略（替代硬截断）：
    1. 不超过上限时直接追加
    2. 超过上限时：头部保留 + 摘要注入 + 尾部保留
    3. 摘要来自 Conversation.summary（由 summary.py 后台生成）
    """
    from pydantic_core import to_json

    if not new_msgs:
        return

    existing = load_pydantic_history(conv)
    updated = existing + new_msgs

    if len(updated) <= _MAX_HISTORY_MESSAGES:
        conv.pydantic_messages = to_json(updated).decode()
        return

    # ── 压缩：头部 + 摘要 + 尾部 ──
    # 先清除旧的摘要注入消息
    cleaned = [m for m in updated if not _has_summary_marker(m)]

    head = cleaned[:_HEAD_KEEP]
    tail = cleaned[-_TAIL_KEEP:]

    parts = list(head)

    # 注入摘要（如有）
    summary_text = summary or conv.summary
    if summary_text and summary_text.strip() and summary_text.strip() != "无重要内容":
        summary_msg = _build_summary_message(summary_text)
        # 标记以便后续清理
        for part in summary_msg.parts:
            if isinstance(part, SystemPromptPart):
                part.content = f"{_SUMMARY_MARKER}\n{part.content}"  # type: ignore[assignment]
                break
        parts.append(summary_msg)

    parts.extend(tail)
    conv.pydantic_messages = to_json(parts).decode()
    logger.info(
        "历史已压缩",
        original=len(updated),
        compressed=len(parts),
        has_summary=bool(summary_text),
        conversation_id=getattr(conv, "conversation_id", None),
    )
