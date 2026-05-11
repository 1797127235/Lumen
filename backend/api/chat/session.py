"""对话会话管理：创建/获取会话、消息历史序列化"""

from __future__ import annotations

from backend.domain.models import Conversation
from backend.logging_config import get_logger

logger = get_logger(__name__)


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


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
    from pydantic_ai import ModelMessagesTypeAdapter

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


def save_pydantic_history(conv, new_msgs: list) -> None:
    """保存消息历史到 Conversation.pydantic_messages（保留最近 30 条）"""
    from pydantic_core import to_json

    if not new_msgs:
        return
    existing = load_pydantic_history(conv)
    updated = (existing + new_msgs)[-30:]
    conv.pydantic_messages = to_json(updated).decode()
