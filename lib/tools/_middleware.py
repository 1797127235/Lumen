"""工具中间件 — 装配时包裹 execute，对应 openhanako tool-wrapper.js。"""

from __future__ import annotations

import dataclasses
import time
from typing import Any

from lib.tools._base import ToolDef, tool_error, tool_ok
from shared.logging import get_logger

logger = get_logger(__name__)

# ── 错误返回检测 ────────────────────────────────────────

# 不参与失败计数的工具（总是只读、非目标导向的辅助工具）
_UTILITY_TOOLS: frozenset[str] = frozenset(
    {
        "tool_search",  # 搜索工具本身不算失败
        "skill_load",  # 加载技能
        "get_profile",  # 读取画像
        "memory_search",  # 搜索记忆
    }
)

_FAILURE_DEGRADATION_THRESHOLD = 8  # 连续无有效产出 N 次后触发降级提示


def _is_error_result(result: Any) -> bool:
    """判断工具返回是否为「无效产出」。

    优先检查 ToolReturn.metadata["error"]（tool_error() 的标准标记），
    兜底检查 return_value 是否以 ❌ 开头。
    """
    if result is None:
        return True

    # ToolReturn 对象 — 直接看 metadata
    if hasattr(result, "metadata") and result.metadata:
        meta = result.metadata if isinstance(result.metadata, dict) else {}
        if meta.get("error") is True:
            return True

    # 提取文本内容
    text = ""
    if hasattr(result, "return_value"):
        text = str(result.return_value)
    elif isinstance(result, str):
        text = result
    else:
        text = str(result)

    # 只有明确以 ❌ 开头的才判定为错误（避免误杀包含 error/403 等词的正常结果）
    return text.startswith("❌")


# ── 常量 ────────────────────────────────────────────────

TOOL_CALLS_LIMIT = 50


# ── 中间件函数 ──────────────────────────────────────────


def wrap_with_logging(tools: list[ToolDef]) -> list[ToolDef]:
    """为每个工具的 execute 加上耗时日志和异常兜底。"""

    def wrap(t: ToolDef) -> ToolDef:
        orig = t.execute

        async def logged(args: dict[str, Any], ctx, _orig=orig, _name=t.name):
            start = time.perf_counter()
            try:
                result = await _orig(args, ctx)
                ms = round((time.perf_counter() - start) * 1000, 1)
                logger.info("tool ok", tool=_name, ms=ms)
                return result
            except Exception as exc:
                ms = round((time.perf_counter() - start) * 1000, 1)
                logger.exception("tool error", tool=_name, ms=ms, error=str(exc))
                return tool_error(f"执行失败: {exc}", "EXEC_ERROR")

        return dataclasses.replace(t, execute=logged)

    return [wrap(t) for t in tools]


def wrap_with_budget(tools: list[ToolDef], limit: int = 20) -> list[ToolDef]:
    """限制单次 agent run 内的工具调用总次数。状态存在 deps.usage_budget。"""

    def wrap(t: ToolDef) -> ToolDef:
        orig = t.execute

        async def budgeted(args: dict[str, Any], ctx, _orig=orig, _name=t.name):
            deps = ctx.deps if hasattr(ctx, "deps") else ctx
            used = deps.usage_budget.get("calls", 0)
            if used >= limit:
                return tool_error(f"工具调用次数已达上限 ({used}/{limit})，请直接回答", "BUDGET")
            result = await _orig(args, ctx)
            deps.usage_budget["calls"] = used + 1
            return result

        return dataclasses.replace(t, execute=budgeted)

    return [wrap(t) for t in tools]


def wrap_with_loop_guard(tools: list[ToolDef]) -> list[ToolDef]:
    """为每个工具的 execute 加上循环保护。"""
    from lib.tools._loop_guard import get_loop_guard

    def wrap(t: ToolDef) -> ToolDef:
        orig = t.execute

        async def guarded(args: dict[str, Any], ctx, _orig=orig, _name=t.name):
            conv_id = None
            if ctx is not None:
                deps = getattr(ctx, "deps", ctx)
                conv_id = getattr(deps, "conversation_id", None)
                if conv_id is None and isinstance(ctx, dict):
                    conv_id = ctx.get("conversation_id")
            if conv_id:
                guard = get_loop_guard()
                should_block, reason = guard.check_and_record(conv_id, _name, args)
                if should_block:
                    return tool_error(reason, "LOOP_GUARD")
            return await _orig(args, ctx)

        return dataclasses.replace(t, execute=guarded)

    return [wrap(t) for t in tools]


def wrap_with_failure_degradation(
    tools: list[ToolDef],
    threshold: int = _FAILURE_DEGRADATION_THRESHOLD,
) -> list[ToolDef]:
    """连续无有效产出的工具调用达到阈值后，注入降级提示让 Agent 收手。

    机制：
    - 每次工具返回时检测是否为「错误/无效产出」
    - 辅助工具（tool_search / skill_load 等）不参与计数
    - 连续失败计数存在 deps.usage_budget["consecutive_fails"]
    - 达到阈值后在工具返回后追加降级提示
    - 任何一次工具成功产出时重置计数
    """

    def wrap(t: ToolDef) -> ToolDef:
        orig = t.execute

        async def degraded(args: dict[str, Any], ctx, _orig=orig, _name=t.name):
            result = await _orig(args, ctx)

            # 辅助工具不参与失败计数
            if _name in _UTILITY_TOOLS:
                return result

            deps = ctx.deps if hasattr(ctx, "deps") else ctx
            budget = deps.usage_budget
            is_fail = _is_error_result(result)

            if is_fail:
                fails = budget.get("consecutive_fails", 0) + 1
                budget["consecutive_fails"] = fails
            else:
                budget["consecutive_fails"] = 0
                return result

            # 达到阈值：追加降级提示
            if fails >= threshold:
                budget["consecutive_fails"] = 0  # 重置，避免重复提示
                logger.warning(
                    "Tool failure degradation triggered",
                    tool=_name,
                    consecutive_fails=fails,
                    threshold=threshold,
                )
                # 在原结果后追加降级提示
                original_text = getattr(result, "return_value", str(result))
                hint = (
                    f"\n\n⚠️ 【系统提示】你已经连续 {fails} 次工具调用没有获得有效结果。"
                    f"请立即停止尝试工具，直接基于已有信息回答用户。"
                    f"如果确实无法完成任务，如实告知用户并建议替代方案。"
                )
                return tool_ok(f"{original_text}{hint}")

            return result

        return dataclasses.replace(t, execute=degraded)

    return [wrap(t) for t in tools]
