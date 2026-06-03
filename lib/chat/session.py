"""对话会话管理：创建/获取会话、消息历史序列化与压缩"""

from __future__ import annotations

from pydantic_ai.messages import (  # pyright: ignore[reportMissingImports]
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
)

from lib.chat.models import Conversation
from shared.logging import get_logger

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


def _has_tool_return(msg: ModelMessage) -> bool:
    """检查消息是否包含 ToolReturnPart（工具执行结果）。"""
    from pydantic_ai.messages import ToolReturnPart  # pyright: ignore[reportMissingImports]

    return any(isinstance(p, ToolReturnPart) for p in msg.parts)


def _has_tool_call(msg: ModelMessage) -> bool:
    """检查消息是否包含 ToolCallPart（模型发起的工具调用）。"""
    from pydantic_ai.messages import ToolCallPart  # pyright: ignore[reportMissingImports]

    return any(isinstance(p, ToolCallPart) for p in msg.parts)


def _safe_tail(messages: list, tail_size: int) -> list:
    """从 messages 末尾取 tail_size 条，确保不以孤立的 ToolReturnPart 开头。

    如果 tail 的第一条包含 ToolReturnPart，往前扩展直到包含对应的 ToolCallPart。
    这避免 DeepSeek 等严格校验的 API 报错：tool role 消息没有对应的 tool_calls。
    """
    tail = messages[-tail_size:]
    if not tail:
        return tail

    # 如果 tail 第一条不是工具结果，直接返回
    if not _has_tool_return(tail[0]):
        return tail

    # 向前找到对应的 ToolCall（ModelResponse with ToolCallPart）
    cutoff = len(messages) - tail_size
    for i in range(cutoff - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, ModelResponse) and _has_tool_call(msg):
            # 找到了，把从这条开始到末尾都算 tail
            return messages[i:]
        if isinstance(msg, ModelRequest) and _has_tool_return(msg):
            # 连续多条工具结果，继续往前找
            continue
        # 遇到非工具消息，停止（不应发生，但安全起见）
        break

    # 没找到对应的 ToolCall，丢弃开头的孤立工具结果
    for idx, m in enumerate(tail):
        if not _has_tool_return(m):
            return tail[idx:]
    return []


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
    """从 Conversation.pydantic_messages 加载消息历史，并修复孤立工具消息。"""
    from pydantic_core import to_json

    if not conv.pydantic_messages:
        return []
    try:
        messages = ModelMessagesTypeAdapter.validate_json(conv.pydantic_messages.encode())
    except Exception as exc:
        logger.warning(
            "消息历史反序列化失败，重置为空",
            error=str(exc),
            conversation_id=getattr(conv, "conversation_id", None),
        )
        return []

    # 修复孤立工具消息：确保每个 tool-return 前面都有对应的 tool-call
    cleaned = _fix_orphaned_tool_messages(messages)
    if len(cleaned) < len(messages):
        # 修复了问题，回写
        conv.pydantic_messages = to_json(cleaned).decode()
        logger.info(
            "修复了孤立工具消息",
            original=len(messages),
            cleaned=len(cleaned),
            conversation_id=getattr(conv, "conversation_id", None),
        )

    return cleaned


def _fix_orphaned_tool_messages(messages: list) -> list:
    """移除没有对应 tool-call 的 tool-return，以及没有对应 tool-return 的 tool-call。

    DeepSeek 等严格 API 要求 tool role 消息必须有前置 tool_calls。
    PydanticAI 在工具失败重试时可能产生 retry-prompt 而非 tool-return，
    导致 tool-call 孤立。
    """
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart  # pyright: ignore[reportMissingImports]

    result = []
    for msg in messages:
        parts = msg.parts if hasattr(msg, "parts") else []

        # 检查是否包含 ToolReturnPart
        has_tool_return = any(isinstance(p, ToolReturnPart) for p in parts)
        has_tool_call = any(isinstance(p, ToolCallPart) for p in parts)

        if has_tool_return:
            # 检查前一条是否是包含 tool-call 的 response
            # 如果 result 为空或最后一条不是含 tool-call 的 response，则跳过
            if not result:
                continue
            last = result[-1]
            last_parts = last.parts if hasattr(last, "parts") else []
            last_has_tool_call = any(isinstance(p, ToolCallPart) for p in last_parts)
            if not last_has_tool_call:
                continue  # 跳过孤立的 tool-return

        if has_tool_call:
            # tool-call 暂时保留，但如果下一条不是 tool-return 则需要清理
            # 这里无法前瞻，先保留，由上面的逻辑在遇到孤立 tool-return 时跳过
            pass

        result.append(msg)

    # 反向扫描：移除末尾没有 tool-return 的 tool-call
    while result:
        last = result[-1]
        last_parts = last.parts if hasattr(last, "parts") else []
        if any(isinstance(p, ToolCallPart) for p in last_parts):
            result.pop()
        else:
            break

    return result


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
    tail = _safe_tail(cleaned, _TAIL_KEEP)

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
