"""上下文预算系统 — 工具返回溢出落盘 + 单轮总量预算。

Layer 0: wrap_with_result_budget 中间件 — 大结果落盘，返回 preview
Layer 1: enforce_turn_budget — 单轮总量检查
Layer 3: prune_tool_results — 旧工具返回摘要 + 保护区 head+tail 截断
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from shared.logging import get_logger

logger = get_logger(__name__)

_RESULT_PERSIST_THRESHOLD = 6_000
_PREVIEW_SIZE = 800


class ToolResultStore:
    """管理工具返回的溢出落盘。纯同步文件 IO，不涉及 async。"""

    def __init__(self, conv_id: str):
        self._dir = Path.home() / ".lumen" / "tool_results" / conv_id

    def save(self, tool_name: str, call_id: str, content: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{call_id}.json"
        path.write_text(
            json.dumps(
                {
                    "tool_name": tool_name,
                    "content": content,
                    "char_count": len(content),
                    "saved_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def load(self, call_id: str) -> str | None:
        path = self._dir / f"{call_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["content"]


def generate_preview(content: str, size: int = _PREVIEW_SIZE) -> str:
    """生成内容预览。"""
    if len(content) <= size:
        return content
    return content[:size] + f"\n...({len(content) - size} chars truncated)..."


def generate_call_id(tool_name: str, args: dict) -> str:
    """生成唯一的调用 ID。"""
    import hashlib

    payload = f"{tool_name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
    return hashlib.md5(payload.encode()).hexdigest()[:12]


# ── Layer 1: 单轮总量预算 ────────────────────────────────────────

_TURN_BUDGET = 12_000


def enforce_turn_budget(
    messages: list[dict],
    conv_id: str,
    budget: int = _TURN_BUDGET,
) -> list[dict]:
    """单轮总量预算控制。

    检查所有 tool 返回内容合计是否超过 budget，超过则落盘。
    Layer 0 已处理单条大结果。本层处理「多条中等结果合计超预算」。
    """
    tool_returns: list[tuple[int, str, int]] = []  # (index, call_id, size)
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if "<persisted-output" in str(content):
                continue
            tool_returns.append((i, msg.get("tool_call_id", f"idx_{i}"), len(str(content))))

    total = sum(sz for _, _, sz in tool_returns)
    if total <= budget:
        return messages

    tool_returns.sort(key=lambda x: x[2], reverse=True)
    store = ToolResultStore(conv_id)
    result = list(messages)
    spilled = 0

    for i, call_id, sz in tool_returns:
        if total <= budget:
            break
        msg = result[i]
        content = str(msg.get("content", ""))

        store.save("turn_budget_spill", call_id, content)
        preview = generate_preview(content, _PREVIEW_SIZE)
        replacement = (
            f'<persisted-output tool="turn_budget_spill" result_id="{call_id}">\n'
            f"单轮总量超预算，完整内容已保存。使用 result_read 工具读取。\n"
            f"预览：\n{preview}\n"
            f"</persisted-output>"
        )

        result[i] = {**msg, "content": replacement}
        total -= sz - len(replacement)
        spilled += 1

    if spilled:
        logger.info("turn_budget_enforced", spilled=spilled, total_chars=total)
    return result


# ── Layer 3: Pre-Request 剪枝（幂等） ──────────────────────────

_SUMMARY_PREFIX = "[PRUNED]"
_PROTECT_TAIL_TURNS = 2
_HEAD_TAIL_BUDGET = 1000


def _head_tail_truncate(text: str, budget: int = _HEAD_TAIL_BUDGET) -> str:
    """Head+tail 截断：保留前后各一半，中间用省略标记连接。"""
    if len(text) <= budget:
        return text
    head = budget // 2
    tail = budget - head
    omitted = len(text) - budget
    return text[:head] + f"\n...({omitted} chars truncated)...\n" + text[-tail:]


def prune_tool_results(
    messages: list[dict],
    protect_tail_turns: int = _PROTECT_TAIL_TURNS,
) -> list[dict]:
    """幂等的工具结果剪枝。

    两层处理：
    - 保护区外（旧 turn）：替换为 [PRUNED] 摘要（150 字符）
    - 保护区内（最近 turn）：对超过 _HEAD_TAIL_BUDGET 的结果做 head+tail 截断

    幂等性保证：
    - 已以 [PRUNED] 开头的结果不会被再次处理
    - 已是 <persisted-output> 的结果不会被处理
    """
    turns = _find_user_turn_boundaries(messages)
    if len(turns) <= protect_tail_turns:
        protected_start = 0
    else:
        protected_start = turns[-protect_tail_turns][0]

    modified = False
    result = list(messages)

    for i in range(len(result)):
        msg = result[i]
        if msg.get("role") != "tool":
            continue

        content = str(msg.get("content", ""))
        if not content or content.startswith(_SUMMARY_PREFIX) or "<persisted-output" in content:
            continue

        if i < protected_start:
            # 保护区外：摘要
            call_id = msg.get("tool_call_id", f"idx_{i}")
            tool_name = _find_tool_name_for_call_id(result, call_id, i)
            summary = f"{_SUMMARY_PREFIX} [{tool_name}] {content[:150].replace(chr(10), ' ').strip()}... ({len(content):,} chars)"
            result[i] = {**msg, "content": summary}
            modified = True
        elif len(content) > _HEAD_TAIL_BUDGET:
            # 保护区内：head+tail 截断
            result[i] = {**msg, "content": _head_tail_truncate(content)}
            modified = True

    return result if modified else messages


def _find_user_turn_boundaries(messages: list[dict]) -> list[tuple[int, int]]:
    """按 user 消息识别 turn 边界。"""
    if not messages:
        return []

    turns: list[tuple[int, int]] = []
    turn_start = 0

    for i, msg in enumerate(messages):
        if i > 0 and msg.get("role") == "user":
            turns.append((turn_start, i - 1))
            turn_start = i

    turns.append((turn_start, len(messages) - 1))
    return turns


def _find_tool_name_for_call_id(messages: list[dict], call_id: str, hint_idx: int) -> str:
    """在 hint_idx 附近查找 tool_call_id 对应的 tool name。"""
    for delta in range(0, min(6, len(messages))):
        for idx in (hint_idx - 1 - delta, hint_idx - 1 + delta):
            if 0 <= idx < len(messages):
                msg = messages[idx]
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        if tc.get("id") == call_id:
                            return tc.get("function", {}).get("name", "unknown")
    return "unknown"


# ── Layer 4: LLM 压缩（替代 _safe_tail） ────────────────────────

_COMPRESS_TRIGGER_MESSAGES = 35
_COMPRESS_TARGET_MESSAGES = 20
_COMPRESS_KEEP_TAIL_TURNS = 4


async def compress_history(
    messages: list[dict],
    target: int = _COMPRESS_TARGET_MESSAGES,
    keep_tail_turns: int = _COMPRESS_KEEP_TAIL_TURNS,
) -> list[dict]:
    """用 LLM 压缩旧 turn，保留最近 keep_tail_turns 个用户轮次不变。

    压缩策略：
    1. 按 user turn 边界切分
    2. 保护最近 keep_tail_turns 个 turn
    3. 对旧 turn 调用 LLM 生成摘要，替换为单条 user message
    4. 调用 MemoryManager.on_pre_compress 注入 provider 上下文
    """
    turns = _find_user_turn_boundaries(messages)
    if len(turns) <= keep_tail_turns:
        return messages

    protected_start = turns[-keep_tail_turns][0]
    old_messages = messages[:protected_start]
    tail_messages = messages[protected_start:]

    if len(old_messages) <= target:
        return messages

    # 尝试 LLM 压缩
    try:
        provider_context = await _get_provider_context(messages)

        conversation_text = _serialize_for_summary(old_messages)

        prompt = (
            "请将以下对话历史压缩为一段简洁的摘要（不超过 500 字）。"
            "保留关键事实、决策和用户偏好。忽略寒暄和重复内容。\n\n"
        )
        if provider_context:
            prompt += f"外部记忆补充：\n{provider_context}\n\n"
        prompt += f"对话历史：\n{conversation_text[:12000]}"

        from core.config import get_settings
        from lib.llm.client import LLMClient

        settings = get_settings()
        llm = LLMClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
        )
        llm_messages = [
            {"role": "system", "content": "你是一个对话摘要助手。只输出摘要文本，不要有前缀或解释。"},
            {"role": "user", "content": prompt},
        ]
        response = await llm.chat(messages=llm_messages)
        summary = response.content or _fallback_summary(old_messages)

        # 将摘要作为 user message 插入
        summary_msg = {"role": "user", "content": f"[历史摘要]\n{summary}"}
        return [summary_msg, *tail_messages]
    except Exception as exc:
        logger.warning("compress_history LLM 调用失败，使用 fallback", error=str(exc))
        return _fallback_compress(old_messages) + tail_messages


def _fallback_compress(messages: list[dict]) -> list[dict]:
    """LLM 不可用时的简单压缩：保留 user + assistant 交替的最近消息。"""
    # 保留最近 10 条消息
    recent = messages[-10:] if len(messages) > 10 else messages
    return [{"role": "user", "content": _fallback_summary(recent)}]


def _fallback_summary(messages: list[dict]) -> str:
    """LLM 不可用时的简单摘要。"""
    parts = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user" and content:
            parts.append(f"用户: {str(content)[:100]}")
        elif role == "assistant" and content:
            parts.append(f"助手: {str(content)[:150]}")

    if not parts:
        return "(对话历史已压缩)"
    return "\n".join(parts[-10:])


def _serialize_for_summary(messages: list[dict]) -> str:
    """将消息序列化为摘要 prompt 可读的文本。"""
    lines = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user" and content:
            lines.append(f"用户: {str(content)[:200]}")
        elif role == "assistant" and content:
            lines.append(f"助手: {str(content)[:200]}")
    return "\n".join(lines)


async def _get_provider_context(messages: list[dict]) -> str:
    """调用 MemoryManager.on_pre_compress 获取外部 provider 上下文。"""
    try:
        from lib.memory import get_memory_manager

        mm = get_memory_manager()
        return await mm.on_pre_compress(messages)
    except Exception:
        return ""
