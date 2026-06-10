"""对话会话管理：创建/获取会话、消息历史序列化与压缩

消息历史安全机制（4 层防御）：
1. _safe_tail() — 按 turn 边界截断，保证原子性
2. sanitize_history() — 保存时清洗，修复结构问题
3. load_pydantic_history() — 加载时再次清洗，防止 DB 中存了脏数据
4. agent_runner.py — 发送 API 前最后一次 sanitize，运行时兜底
"""

from __future__ import annotations

from pydantic_ai.messages import (  # pyright: ignore[reportMissingImports]
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
)

from lib.chat.models import Conversation
from shared.logging import get_logger

logger = get_logger(__name__)

# ── 历史压缩常量 ──────────────────────────────────────
_MAX_HISTORY_MESSAGES = 40  # 硬上限（之前是 30）
_TOOL_RESULT_CHAR_BUDGET = 2000  # 工具返回截断阈值


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _truncate_tool_result(content: object) -> str:
    """截断工具返回内容，防止历史累积膨胀。"""
    text = content if isinstance(content, str) else str(content)
    if len(text) <= _TOOL_RESULT_CHAR_BUDGET:
        return text
    return text[:_TOOL_RESULT_CHAR_BUDGET] + f"\n...({len(text) - _TOOL_RESULT_CHAR_BUDGET} chars truncated)..."


def _truncate_tool_returns_in_messages(messages: list[ModelMessage]) -> list[ModelMessage]:
    """对消息列表中的所有 ToolReturnPart 进行截断，返回新列表。"""
    from dataclasses import replace

    from pydantic_ai.messages import (  # pyright: ignore[reportMissingImports]
        RetryPromptPart,
        ToolReturnPart,
    )

    result: list[ModelMessage] = []
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            result.append(msg)
            continue

        has_tool = any(isinstance(p, ToolReturnPart | RetryPromptPart) for p in msg.parts)
        if not has_tool:
            result.append(msg)
            continue

        new_parts = []
        for p in msg.parts:
            if isinstance(p, ToolReturnPart | RetryPromptPart):
                truncated = _truncate_tool_result(getattr(p, "content", ""))
                new_parts.append(replace(p, content=truncated))
            else:
                new_parts.append(p)

        result.append(replace(msg, parts=new_parts))
    return result


# ── Part 类型检查辅助 ─────────────────────────────────


def _has_tool_return(msg: ModelMessage) -> bool:
    """检查消息是否包含 ToolReturnPart 或 RetryPromptPart（工具执行结果）。"""
    from pydantic_ai.messages import RetryPromptPart, ToolReturnPart  # pyright: ignore[reportMissingImports]

    return any(isinstance(p, ToolReturnPart | RetryPromptPart) for p in msg.parts)


def _has_tool_call(msg: ModelMessage) -> bool:
    """检查消息是否包含 ToolCallPart（模型发起的工具调用）。"""
    from pydantic_ai.messages import ToolCallPart  # pyright: ignore[reportMissingImports]

    return any(isinstance(p, ToolCallPart) for p in msg.parts)


def _get_tool_call_ids(msg: ModelMessage) -> set[str]:
    """收集消息中所有 ToolCallPart 的 tool_call_id。"""
    from pydantic_ai.messages import ToolCallPart  # pyright: ignore[reportMissingImports]

    return {
        p.tool_call_id
        for p in (msg.parts if hasattr(msg, "parts") else [])
        if isinstance(p, ToolCallPart) and p.tool_call_id
    }


def _get_tool_response_ids(msg: ModelMessage) -> set[str]:
    """收集消息中所有 ToolReturnPart/RetryPromptPart 关联的 tool_call_id。"""
    from pydantic_ai.messages import RetryPromptPart, ToolReturnPart  # pyright: ignore[reportMissingImports]

    return {
        p.tool_call_id
        for p in (msg.parts if hasattr(msg, "parts") else [])
        if isinstance(p, ToolReturnPart | RetryPromptPart) and hasattr(p, "tool_call_id") and p.tool_call_id
    }


# ── Layer 1: Turn 感知截断 ─────────────────────────────


def _find_turn_boundaries(messages: list[ModelMessage]) -> list[tuple[int, int]]:
    """识别消息历史中的 turn 边界。

    一个 turn = ModelRequest(user) + 后续的 ModelResponse(tool_calls) + ModelRequest(tool_returns)
    直到下一个 ModelRequest(user) 开始新 turn。

    返回 [(start_idx, end_idx_inclusive), ...] 列表。
    """
    if not messages:
        return []

    turns: list[tuple[int, int]] = []
    turn_start = 0

    for i, msg in enumerate(messages):
        # 新 turn 开始于非首条的 ModelRequest（首条是第一个 turn 的起点）
        if i > 0 and isinstance(msg, ModelRequest):
            # 只有当这条 ModelRequest 包含 user parts（不是纯 tool-return）时才算新 turn
            # 但为安全起见，任何 ModelRequest 都视为 turn 起点
            turns.append((turn_start, i - 1))
            turn_start = i

    # 最后一个 turn
    turns.append((turn_start, len(messages) - 1))
    return turns


def _safe_tail(messages: list, tail_size: int) -> list:
    """从 messages 末尾取最多 tail_size 条，按 turn 边界截断。

    Turn 是原子单元：ModelRequest(user) → ModelResponse(tool_calls) → ModelRequest(tool_returns)。
    截断永远不会在 turn 中间切开。
    """
    if tail_size <= 0 or not messages:
        return []

    # 如果总条数已在限额内，直接返回（但仍然 sanitize）
    if len(messages) <= tail_size:
        return sanitize_history(messages)

    # 按 turn 边界找到合适的截断点
    turns = _find_turn_boundaries(messages)

    # 从后往前累加 turn 大小，直到超过 tail_size
    selected_turns: list[tuple[int, int]] = []
    accumulated = 0

    for start, end in reversed(turns):
        turn_size = end - start + 1
        if accumulated + turn_size > tail_size and selected_turns:
            # 加入这个 turn 会超限，且已有更近的 turn，停止
            break
        selected_turns.append((start, end))
        accumulated += turn_size

    if not selected_turns:
        # 至少保留最后一个 turn
        selected_turns.append(turns[-1])

    selected_turns.reverse()

    # 拼接选中的 turns
    result: list[ModelMessage] = []
    for start, end in selected_turns:
        result.extend(messages[start : end + 1])

    # 截断后再做一次 sanitize 确保干净
    return sanitize_history(result)


# ── Layer 2/3: sanitize_history — 顺序验证器 ──────────────


def sanitize_history(messages: list) -> list:
    """修复消息历史中的结构问题，确保符合 OpenAI Chat API 消息顺序合约。

    修复规则（顺序扫描）：
    1. 历史必须以 ModelRequest 开头（不是 assistant/tool）
    2. 每个 assistant(tool_calls) 后面必须紧跟对应数量的 tool(response) 消息
    3. tool(response) 消息前面必须有对应的 assistant(tool_calls)
    4. 孤立的 tool_call 在末尾会被移除
    5. RetryPromptPart 视为 tool-return 的等价物

    这是唯一的历史清洗函数，替代旧的 _fix_orphaned_tool_messages。
    """
    if not messages:
        return []

    from pydantic_ai.messages import (  # pyright: ignore[reportMissingImports]
        RetryPromptPart,
        ToolCallPart,
        ToolReturnPart,
    )

    # ── Phase 1: 跳过开头的非 ModelRequest 消息 ──
    start_idx = 0
    while start_idx < len(messages) and not isinstance(messages[start_idx], ModelRequest):
        start_idx += 1

    if start_idx >= len(messages):
        logger.warning("sanitize: 消息历史中没有任何 ModelRequest，清空历史")
        return []

    if start_idx > 0:
        logger.info(
            "sanitize: 跳过开头的非 ModelRequest 消息",
            skipped=start_idx,
        )

    result: list[ModelMessage] = list(messages[start_idx:])

    # ── Phase 1.5: 过滤旧的 context frame / focus 消息 ──
    # context frame（L0+L1+L2）和 <current-focus> 是每轮运行时注入的，
    # 不应持久化到历史中。过滤掉历史里残留的 frame/focus 消息。
    from pydantic_ai.messages import UserPromptPart  # pyright: ignore[reportMissingImports]

    _filtered = []
    for msg in result:
        if isinstance(msg, ModelRequest):
            # 检查是否包含 <current-focus> 标签
            _is_focus = False
            for p in msg.parts if hasattr(msg, "parts") else []:
                if isinstance(p, UserPromptPart):
                    content = getattr(p, "content", "")
                    if isinstance(content, str) and ("<current-focus>" in content or "</current-focus>" in content):
                        _is_focus = True
                        break
            if _is_focus:
                logger.debug("sanitize: 移除残留的 focus 消息")
                continue
        _filtered.append(msg)
    result = _filtered

    # ── Phase 2: 顺序验证 — tool_call ↔ tool_response 配对 ──
    cleaned: list[ModelMessage] = []
    i = 0

    while i < len(result):
        msg = result[i]

        if isinstance(msg, ModelRequest):
            # 检查这条 ModelRequest 是否包含 tool-return parts
            tool_response_ids = _get_tool_response_ids(msg)

            if tool_response_ids:
                # 这是 tool response 消息，需要验证前面有对应的 tool_calls
                # 找到最近一条 assistant(tool_call)
                pending_call_ids: set[str] = set()
                for j in range(len(cleaned) - 1, -1, -1):
                    if isinstance(cleaned[j], ModelResponse):
                        pending_call_ids = _get_tool_call_ids(cleaned[j])
                        break

                # 只保留有对应 tool_call 的 response parts
                if not pending_call_ids:
                    # 没有对应的 tool_call → 孤立 tool response，跳过这条消息
                    logger.debug(
                        "sanitize: 移除孤立的 tool response (无前置 tool_call)",
                        tool_call_ids=tool_response_ids,
                    )
                    i += 1
                    continue

                # 过滤消息中的 parts，只保留有匹配 tool_call 的
                valid_parts = [
                    p
                    for p in (msg.parts if hasattr(msg, "parts") else [])
                    if not isinstance(p, ToolReturnPart | RetryPromptPart)
                    or (hasattr(p, "tool_call_id") and p.tool_call_id in pending_call_ids)
                ]

                if not any(isinstance(p, ToolReturnPart | RetryPromptPart) for p in valid_parts):
                    # 所有 tool-return 都不匹配，跳过
                    logger.debug(
                        "sanitize: 移除不匹配的 tool response",
                        expected_ids=pending_call_ids,
                        got_ids=tool_response_ids,
                    )
                    i += 1
                    continue

                if len(valid_parts) < len(msg.parts):
                    # 部分匹配，重建消息
                    new_msg = ModelRequest(parts=valid_parts)  # type: ignore[call-arg]
                    cleaned.append(new_msg)
                else:
                    cleaned.append(msg)
                i += 1
                continue
            else:
                # 纯 user 消息，直接保留
                cleaned.append(msg)
                i += 1
                continue

        elif isinstance(msg, ModelResponse):
            tool_call_ids = _get_tool_call_ids(msg)

            if tool_call_ids:
                # assistant(tool_calls) — 检查后续是否有对应的 tool responses
                # 向前看：收集紧跟的 tool response 消息中的 response ids
                responded_ids: set[str] = set()
                response_msgs: list[int] = []  # index in result
                j = i + 1
                while j < len(result):
                    next_msg = result[j]
                    if isinstance(next_msg, ModelRequest):
                        rids = _get_tool_response_ids(next_msg)
                        if rids:
                            responded_ids.update(rids)
                            response_msgs.append(j)
                            j += 1
                            continue
                    # 不是 tool response → 停止扫描
                    break

                # 判断哪些 tool_calls 有对应 response
                satisfied_ids = tool_call_ids & responded_ids
                unsatisfied_ids = tool_call_ids - satisfied_ids

                if unsatisfied_ids:
                    # 有未满足的 tool_call
                    if not satisfied_ids:
                        # 全部未满足 → 整个 assistant(tool_calls) + 预期 response 都跳过
                        logger.debug(
                            "sanitize: 移除无 response 的 tool_call 消息",
                            tool_call_ids=tool_call_ids,
                        )
                        i += 1
                        continue
                    else:
                        # 部分满足 → 过滤掉未满足的 tool_call parts
                        valid_parts = [
                            p
                            for p in (msg.parts if hasattr(msg, "parts") else [])
                            if not isinstance(p, ToolCallPart) or (p.tool_call_id in satisfied_ids)
                        ]
                        # 如果只剩 TextPart 没有 ToolCallPart 了，仍保留
                        new_msg = ModelResponse(parts=valid_parts)  # type: ignore[call-arg]
                        cleaned.append(new_msg)
                        # 只添加匹配的 response 消息
                        for ridx in response_msgs:
                            rmsg = result[ridx]
                            rids = _get_tool_response_ids(rmsg)
                            if rids & satisfied_ids:
                                cleaned.append(rmsg)
                        i = j
                        continue
                else:
                    # 全部满足 → 正常添加 assistant + responses
                    cleaned.append(msg)
                    for ridx in response_msgs:
                        cleaned.append(result[ridx])
                    i = j
                    continue
            else:
                # 纯 assistant 文本响应，直接保留
                cleaned.append(msg)
                i += 1
                continue
        else:
            # 未知消息类型，保守保留
            cleaned.append(msg)
            i += 1
            continue

    # ── Phase 3: 反向清理末尾孤立的 tool_call ──
    while cleaned:
        last = cleaned[-1]
        if isinstance(last, ModelResponse) and _has_tool_call(last) and not _has_tool_return(last):
            cleaned.pop()
        else:
            break

    # ── Phase 4: 再次确保开头是 ModelRequest ──
    while cleaned and not isinstance(cleaned[0], ModelRequest):
        cleaned.pop(0)

    if len(cleaned) < len(messages):
        logger.info(
            "sanitize: 清理了消息历史",
            original=len(messages),
            cleaned=len(cleaned),
            removed=len(messages) - len(cleaned),
        )

    return cleaned


# ── 会话管理 ────────────────────────────────────────


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
    """从 Conversation.pydantic_messages 加载消息历史，并 sanitize 修复结构问题。"""
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

    # Layer 3: 加载时 sanitize
    cleaned = sanitize_history(messages)
    if len(cleaned) < len(messages):
        # 修复了问题，回写干净的历史
        conv.pydantic_messages = to_json(cleaned).decode()
        logger.info(
            "加载时修复了消息历史",
            original=len(messages),
            cleaned=len(cleaned),
            conversation_id=getattr(conv, "conversation_id", None),
        )

    # ── 诊断：统计历史消息构成 ──
    _log_history_stats(cleaned, getattr(conv, "conversation_id", None))

    return cleaned


def save_pydantic_history(conv, new_msgs: list) -> None:
    """保存消息历史到 Conversation.pydantic_messages。

    策略：不超过上限时直接追加，超过时按 turn 边界截断。
    跨轮上下文由 about_you.md / memory.md 提供，不再注入对话摘要。
    """
    from pydantic_core import to_json

    if not new_msgs:
        return

    # 截断新消息中的 tool return，防止历史膨胀
    truncated_new_msgs = _truncate_tool_returns_in_messages(new_msgs)

    existing = load_pydantic_history(conv)
    updated = existing + truncated_new_msgs

    # Layer 2: 保存时 sanitize
    updated = sanitize_history(updated)

    if len(updated) <= _MAX_HISTORY_MESSAGES:
        conv.pydantic_messages = to_json(updated).decode()
        return

    # ── 按 turn 边界截断 ──
    tail = _safe_tail(updated, _MAX_HISTORY_MESSAGES)
    conv.pydantic_messages = to_json(tail).decode()
    logger.info(
        "历史已截断",
        original=len(updated),
        truncated=len(tail),
        conversation_id=getattr(conv, "conversation_id", None),
    )


# ── 诊断：历史消息统计 ──────────────────────────────────────────────


def _log_history_stats(messages: list, conversation_id: str | None) -> None:
    """分析消息列表构成，诊断 token 膨胀原因。"""
    from pydantic_ai.messages import (  # pyright: ignore[reportMissingImports]
        RetryPromptPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    total = len(messages)
    tool_return_count = 0
    tool_return_chars = 0
    tool_call_count = 0
    user_count = 0
    user_chars = 0
    assistant_text_count = 0
    assistant_text_chars = 0
    context_frame_count = 0
    context_frame_chars = 0

    for msg in messages:
        if isinstance(msg, ModelRequest):
            has_tool = any(isinstance(p, ToolReturnPart | RetryPromptPart) for p in msg.parts)
            if has_tool:
                for p in msg.parts:
                    if isinstance(p, ToolReturnPart | RetryPromptPart):
                        tool_return_count += 1
                        content = getattr(p, "content", "")
                        tool_return_chars += len(str(content))
            else:
                for p in msg.parts:
                    if isinstance(p, UserPromptPart):
                        content = str(getattr(p, "content", ""))
                        user_count += 1
                        user_chars += len(content)
                        # 检测 context frame（含特定标记）
                        if "当前时间：" in content or "# 用户记忆" in content or "<current-focus>" in content:
                            context_frame_count += 1
                            context_frame_chars += len(content)

        elif isinstance(msg, ModelResponse):
            has_tool_calls = any(isinstance(p, ToolCallPart) for p in msg.parts)
            if has_tool_calls:
                tool_call_count += sum(1 for p in msg.parts if isinstance(p, ToolCallPart))
            for p in msg.parts:
                if type(p).__name__ == "TextPart":
                    content = str(getattr(p, "content", ""))
                    assistant_text_count += 1
                    assistant_text_chars += len(content)

    logger.info(
        "历史消息统计",
        conversation_id=conversation_id,
        total_messages=total,
        tool_return_count=tool_return_count,
        tool_return_chars=tool_return_chars,
        tool_call_count=tool_call_count,
        user_count=user_count,
        user_chars=user_chars,
        assistant_text_count=assistant_text_count,
        assistant_text_chars=assistant_text_chars,
        context_frame_count=context_frame_count,
        context_frame_chars=context_frame_chars,
        tool_return_pct=round(
            tool_return_chars / max(1, user_chars + assistant_text_chars + tool_return_chars) * 100, 1
        ),
    )
