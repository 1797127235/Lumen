"""工具调度器 — 统一执行链的中心。

dispatcher 负责"如何执行工具"，handler 只负责"业务逻辑本身"。
"""

from __future__ import annotations

import time
from typing import Any

from backend.core.logging import get_logger
from backend.modules.agent.tools.core.context import ToolRuntimeContext
from backend.modules.agent.tools.core.policies import (
    ApprovalPolicy,
    BudgetPolicy,
    LoopGuardPolicy,
    PathPolicy,
    ResultPolicy,
)
from backend.modules.agent.tools.core.registry import ToolRegistry

logger = get_logger(__name__)


class ToolDispatcher:
    """中央工具调度器。

    统一执行链：
    1. 查找 ToolDefinition
    2. 预算检查
    3. 循环检测
    4. 路径归一化（如果工具有 path 参数）
    5. 执行 handler
    6. 记录 trace
    7. 返回标准化结果
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def dispatch(
        self,
        tool_name: str,
        args: dict[str, Any],
        ctx: ToolRuntimeContext,
    ) -> str:
        """调度执行一个工具。

        Args:
            tool_name: 工具名（字符串 ID）
            args: Agent 传入的参数
            ctx: 运行时上下文

        Returns:
            工具执行结果（字符串）
        """
        start_time = time.perf_counter()
        trace_entry: dict[str, Any] = {
            "tool": tool_name,
            "args": self._summarize_args(args),
            "start_time": start_time,
        }

        # ── Step 1: 查找 ToolDefinition ──
        tool = self.registry.get(tool_name)
        if tool is None:
            msg = ResultPolicy.format_error(
                f"未知工具 '{tool_name}'。可用工具: {sorted(self.registry.list_tools())}",
                "UNKNOWN_TOOL",
            )
            trace_entry.update({"ok": False, "error": msg, "duration_ms": 0})
            ctx.trace_sink.append(trace_entry)
            return msg

        # ── Step 2: 预算检查 ──
        ok, msg = BudgetPolicy.check(tool, ctx)
        if not ok:
            trace_entry.update({"ok": False, "error": msg, "duration_ms": 0})
            ctx.trace_sink.append(trace_entry)
            return ResultPolicy.format_error(msg, "BUDGET_EXCEEDED")

        # ── Step 3: 循环检测 ──
        ok, msg = LoopGuardPolicy.check(tool, ctx, args)
        if not ok:
            trace_entry.update({"ok": False, "error": msg, "duration_ms": 0})
            ctx.trace_sink.append(trace_entry)
            return ResultPolicy.format_error(msg, "LOOP_DETECTED")

        # ── Step 4: 路径归一化（如果工具有 path 参数）──
        resolved_path: Any = None
        if "path" in args:
            path_str = args.get("path", "")
            if path_str:
                resolved_path, path_error = PathPolicy.resolve(path_str, ctx)
                if path_error:
                    LoopGuardPolicy.record(tool, ctx, args, ok=False)
                    msg = ResultPolicy.format_error(path_error, "PATH_ERROR")
                    trace_entry.update({"ok": False, "error": msg, "duration_ms": 0})
                    ctx.trace_sink.append(trace_entry)
                    return msg
                # 替换为解析后的绝对路径
                args = dict(args)
                args["_resolved_path"] = resolved_path

        # ── Step 4.5: 审批检查 ──
        needs_approval, reason = ApprovalPolicy.check(tool)
        if needs_approval:
            msg = ResultPolicy.format_error(f"工具 '{tool_name}' 需要用户审批：{reason}", "APPROVAL_REQUIRED")
            trace_entry.update({"ok": False, "error": msg, "duration_ms": 0})
            ctx.trace_sink.append(trace_entry)
            # 审批不通过不消耗预算，但记录循环检测
            LoopGuardPolicy.record(tool, ctx, args, ok=False)
            return msg

        # ── Step 5: 执行 handler ──
        if tool.handler is None:
            LoopGuardPolicy.record(tool, ctx, args, ok=False)
            msg = ResultPolicy.format_error(f"工具 '{tool_name}' 未绑定 handler", "NO_HANDLER")
            trace_entry.update({"ok": False, "error": msg, "duration_ms": 0})
            ctx.trace_sink.append(trace_entry)
            return msg

        try:
            result = await tool.handler(args, ctx)
            ok = True
            error = ""
        except Exception as exc:
            logger.exception("Tool handler failed", tool=tool_name, error=str(exc))
            result = ResultPolicy.format_error(f"执行失败: {exc}", "EXEC_ERROR")
            ok = False
            error = str(exc)

        # ── Step 6: 记录 trace ──
        duration_ms = (time.perf_counter() - start_time) * 1000
        trace_entry.update(
            {
                "ok": ok,
                "error": error,
                "duration_ms": round(duration_ms, 2),
                "result_length": len(result),
            }
        )
        ctx.trace_sink.append(trace_entry)

        # ── Step 7: 更新状态 ──
        BudgetPolicy.consume(ctx)
        LoopGuardPolicy.record(tool, ctx, args, ok=ok)

        if ok:
            logger.info(
                "Tool succeeded",
                tool=tool_name,
                user_id=ctx.user_id,
                duration_ms=round(duration_ms, 2),
                result_length=len(result),
            )
        else:
            logger.warning(
                "Tool failed",
                tool=tool_name,
                user_id=ctx.user_id,
                error=error or result,
                duration_ms=round(duration_ms, 2),
            )

        return result

    # -- 内部辅助 --

    _SENSITIVE_KEYS: frozenset[str] = frozenset(
        {"api_key", "token", "password", "secret", "authorization", "access_token"}
    )

    def _summarize_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """脱敏 + 截断参数，用于日志。"""
        summary: dict[str, Any] = {}
        for k, v in args.items():
            if k.lower() in self._SENSITIVE_KEYS:
                summary[k] = "***"
            elif isinstance(v, str) and len(v) > 200:
                summary[k] = v[:200] + "..."
            else:
                summary[k] = v
        return summary
