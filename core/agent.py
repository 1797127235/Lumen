"""Lumen Agent — 核心编排（参照 akashic-agent 设计）

纯函数式 ReAct 循环 + 渐进式上下文裁剪，无类、无 generator、无 PydanticAI。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from core.config import get_settings, load_user_config
from lib.agent.types import AgentContext
from lib.bus.event_bus import ToolCallCompleted, ToolCallStarted
from lib.llm.client import ContextLengthError, LLMClient, LLMResponse, ToolCall
from lib.tools._registry import ToolRegistry, get_tool_registry
from shared.logging import get_logger

logger = get_logger(__name__)

_TOOL_CALLS_LIMIT = 20
_MAX_ITERATIONS = 12
_CONSECUTIVE_TOOL_FAILURE_LIMIT = 2

# 渐进式裁剪比率（参照 akashic-agent _SAFETY_RETRY_RATIOS）
_SAFETY_RETRY_RATIOS = (1.0, 0.5, 0.0)


@dataclass
class AgentResult:
    content: str
    tool_chain: list[dict[str, Any]] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
#  渐进式裁剪 + ReAct 循环
# ═══════════════════════════════════════════════════════════════════


async def run_agent(
    messages: list[dict[str, Any]],
    ctx: AgentContext,
) -> AgentResult:
    """渐进式裁剪外壳：ContextLengthError 时逐步缩减历史重试。

    参照 akashic-agent PassiveTurnPipeline.run_turn() 的 attempt plan 机制：
    1. ratio=1.0 — 完整消息
    2. ratio=0.5 — 保留 system + 后半段消息
    3. ratio=0.0 — 仅 system + 最后一条 user message
    """
    total = len(messages)

    for ratio in _SAFETY_RETRY_RATIOS:
        window = max(2, int(total * ratio))
        if window >= total:
            attempt = list(messages)
        else:
            attempt = [messages[0], *messages[-(window - 1) :]]

        try:
            return await _run_react_loop(attempt, ctx)
        except ContextLengthError:
            if ratio == _SAFETY_RETRY_RATIOS[-1]:
                logger.warning(
                    "所有裁剪计划均失败",
                    conversation_id=ctx.conversation_id,
                    total_messages=total,
                )
                return AgentResult(content="上下文过长，请尝试新建对话。")
            logger.warning(
                "ContextLengthError，裁剪重试",
                ratio=ratio,
                window=window,
                conversation_id=ctx.conversation_id,
            )

    return AgentResult(content="上下文过长，请尝试新建对话。")


async def _run_react_loop(
    messages: list[dict[str, Any]],
    ctx: AgentContext,
) -> AgentResult:
    """运行 ReAct 循环（单次 attempt，不处理 ContextLengthError）。"""
    registry = get_tool_registry()
    registry.set_context(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id or "",
        workspace_root=ctx.workspace_root,
        db=ctx.db,
        source_platform=ctx.source_platform,
        progress_emitter=ctx.progress_emitter,
    )

    consecutive_failures = 0
    tool_chain: list[dict[str, Any]] = []
    total_usage: dict[str, int] = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    def _accumulate(resp: LLMResponse) -> None:
        for k in total_usage:
            total_usage[k] += resp.usage.get(k, 0)

    for iteration in range(_MAX_ITERATIONS):
        visible, schemas = _visible_tool_schemas(registry, ctx.conversation_id)
        logger.info("[ReAct] 第%d轮，可见工具=%d", iteration + 1, len(visible))

        response = await _call_llm(
            messages,
            schemas,
            conversation_id=ctx.conversation_id,
            usage_scope=f"call:{iteration + 1}",
        )
        _accumulate(response)

        if not response.tool_calls:
            _log_usage(total_usage, ctx.conversation_id)
            return AgentResult(content=response.content or "", tool_chain=tool_chain)

        messages.append(_build_assistant_message(response))
        calls_record: list[dict[str, Any]] = []
        for tc in response.tool_calls:
            result = await _execute_tool(tc, ctx)
            result_str = str(result)
            messages.append(_build_tool_message(tc, result_str))
            calls_record.append(
                {
                    "call_id": tc.id,
                    "name": tc.name,
                    "arguments": dict(tc.arguments),
                    "result": result_str,
                }
            )
            if _is_tool_failure(result):
                consecutive_failures += 1
            else:
                consecutive_failures = 0

        tool_chain.append(
            {
                "text": response.content,
                "calls": calls_record,
            }
        )

        if consecutive_failures >= _CONSECUTIVE_TOOL_FAILURE_LIMIT:
            logger.warning(
                "[ReAct] 连续工具失败，强制最终回答",
                consecutive_failures=consecutive_failures,
            )
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"已经连续 {consecutive_failures} 次工具调用失败或无效。"
                        "立即停止调用工具，基于已有信息直接回答用户；"
                        "如果无法完成，明确说明卡在哪里。"
                    ),
                }
            )
            response = await _call_llm(
                messages,
                None,
                conversation_id=ctx.conversation_id,
                usage_scope="call:final_after_failures",
            )
            _accumulate(response)
            _log_usage(total_usage, ctx.conversation_id)
            return AgentResult(content=response.content or "", tool_chain=tool_chain)

    # 达到最大轮次，强制最终回答
    logger.warning("[ReAct] 达到最大轮次，强制最终回答")
    messages.append({"role": "user", "content": "请基于已有信息直接回答用户，不要再调用任何工具。"})
    response = await _call_llm(
        messages,
        None,
        conversation_id=ctx.conversation_id,
        usage_scope="call:final_after_limit",
    )
    _accumulate(response)
    _log_usage(total_usage, ctx.conversation_id)
    return AgentResult(content=response.content or "", tool_chain=tool_chain)


def _visible_tool_schemas(
    registry: ToolRegistry,
    conversation_id: str | None,
) -> tuple[set[str], list[dict[str, Any]]]:
    """Return currently visible tool names and schemas.

    tool_search mutates ToolDiscoveryState during a ReAct run, so schemas must
    be recomputed each iteration instead of being captured once at startup.
    """

    from lib.tools._discovery import get_tool_discovery_state

    discovery = get_tool_discovery_state()
    visible = registry.get_always_on_names() | set(discovery.get_visible(conversation_id))
    return visible, registry.get_schemas(names=visible)


def _is_tool_failure(result: str) -> bool:
    text = str(result or "").lstrip()
    return text.startswith("❌") or "[BUDGET]" in text or "[LOOP_GUARD]" in text or "Unknown tool name" in text


def _log_usage(total: dict[str, int], conversation_id: str | None, *, scope: str = "aggregate") -> None:
    if sum(total.values()) == 0:
        return
    input_t = total["input"]
    cache_r = total["cache_read"]
    hit_pct = round(cache_r / input_t * 100, 1) if input_t else 0.0
    logger.info(
        "LLM usage",
        scope=scope,
        input=input_t,
        output=total["output"],
        cache_read=cache_r,
        cache_write=total["cache_write"],
        cache_hit_pct=f"{hit_pct}%",
        conversation_id=conversation_id,
    )


async def _call_llm(
    messages: list[dict[str, Any]],
    schemas: list[dict[str, Any]] | None,
    *,
    conversation_id: str | None = None,
    usage_scope: str = "call",
) -> LLMResponse:
    """调用 LLM。"""
    llm = _get_llm()
    response = await llm.chat(
        messages=messages,
        tools=schemas if schemas else None,
    )
    _log_usage(response.usage, conversation_id, scope=usage_scope)
    return response


async def _execute_tool(tc: ToolCall, ctx: AgentContext) -> str:
    """执行单个工具，并发送 EventBus 事件供 UI 展示。"""
    used = ctx.usage_budget.get("calls", 0)
    if used >= _TOOL_CALLS_LIMIT:
        return "❌[BUDGET] 工具调用次数已达上限"

    # 发送开始事件
    event_bus = getattr(ctx, "event_bus", None)
    if event_bus:
        await event_bus.observe(
            ToolCallStarted(
                session_key=f"{ctx.source_platform}:{ctx.conversation_id or ctx.user_id}",
                channel=ctx.source_platform,
                chat_id=ctx.conversation_id or ctx.user_id,
                call_id=tc.id,
                tool_name=tc.name,
                arguments=dict(tc.arguments),
            )
        )

    logger.info("[工具执行→] %s", tc.name)
    status = "done"
    result_preview = ""
    try:
        registry = get_tool_registry()
        result = await registry.execute(tc.name, tc.arguments, ctx)
        ctx.usage_budget["calls"] = used + 1
        result_preview = str(result)[:200] if result else ""
        logger.info("[工具结果←] %s", tc.name)
        result_str = str(result)
    except Exception as exc:
        logger.exception("工具执行失败", tool=tc.name)
        status = "error"
        result_preview = str(exc)[:200]
        result_str = f"❌ 执行失败: {exc}"

    # 发送完成事件
    if event_bus:
        await event_bus.observe(
            ToolCallCompleted(
                session_key=f"{ctx.source_platform}:{ctx.conversation_id or ctx.user_id}",
                channel=ctx.source_platform,
                chat_id=ctx.conversation_id or ctx.user_id,
                call_id=tc.id,
                tool_name=tc.name,
                status=status,
                result_preview=result_preview,
            )
        )

    return result_str


def _build_assistant_message(response: LLMResponse) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": response.content,
    }
    if response.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in response.tool_calls
        ]
    return msg


def _build_tool_message(tc: ToolCall, result: str) -> dict[str, Any]:
    from lib.session.manager import _truncate_tool_result

    return {
        "role": "tool",
        "tool_call_id": tc.id,
        "content": _truncate_tool_result(result),
    }


# ── LLM 客户端单例 ──────────────────────────────────────────────────

_llm: LLMClient | None = None
_agent_generation: int = 0
_config_fingerprint: str = ""


def _get_config_fingerprint() -> str:
    settings = get_settings()
    user_cfg = load_user_config()
    parts = [
        settings.llm_api_key,
        settings.llm_base_url,
        json.dumps(user_cfg.get("providers") or {}, sort_keys=True),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _get_llm() -> LLMClient:
    global _llm, _agent_generation, _config_fingerprint

    current_fp = _get_config_fingerprint()
    if current_fp != _config_fingerprint:
        logger.info("配置变更，重建 LLM 客户端")
        _llm = None
        _config_fingerprint = current_fp

    if _llm is None:
        from lib.tools.factory import register_all_tools

        register_all_tools()
        settings = get_settings()
        _llm = LLMClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
        )
        _agent_generation += 1
        logger.info("LLM 客户端已构建", generation=_agent_generation)

    return _llm


def get_agent_generation() -> int:
    return _agent_generation


# ── Worker Agent（用于 delegate）─────────────────────────────────────


async def run_worker_agent(
    messages: list[dict[str, Any]],
    ctx: AgentContext,
    tool_names: list[str],
) -> AgentResult:
    """运行受限工具集的 worker Agent。"""
    registry = get_tool_registry()
    filtered = ToolRegistry()
    for name in tool_names:
        tool = registry.get_tool(name)
        if tool:
            filtered.register(tool)

    filtered.set_context(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id or "",
        workspace_root=ctx.workspace_root,
        db=ctx.db,
        source_platform=ctx.source_platform,
        progress_emitter=ctx.progress_emitter,
    )

    visible = filtered.get_always_on_names()
    schemas = filtered.get_schemas(names=visible)

    tool_chain: list[dict[str, Any]] = []
    for _iteration in range(5):
        response = await _call_llm(messages, schemas)
        if not response.tool_calls:
            return AgentResult(content=response.content or "", tool_chain=tool_chain)

        messages.append(_build_assistant_message(response))
        calls_record: list[dict[str, Any]] = []
        for tc in response.tool_calls:
            result = await filtered.execute(tc.name, tc.arguments, ctx)
            messages.append(_build_tool_message(tc, str(result)))
            calls_record.append(
                {
                    "call_id": tc.id,
                    "name": tc.name,
                    "arguments": dict(tc.arguments),
                    "result": str(result),
                }
            )
        tool_chain.append({"text": response.content, "calls": calls_record})

    return AgentResult(content="（Worker Agent 达到最大轮次）", tool_chain=tool_chain)
