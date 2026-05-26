"""工具中间件 — 装配时包裹 execute，对应 openhanako tool-wrapper.js。"""

from __future__ import annotations

import dataclasses
import time
from typing import Any

from lib.tools._base import ToolDef, tool_error
from shared.logging import get_logger

logger = get_logger(__name__)


def wrap_with_logging(tools: list[ToolDef]) -> list[ToolDef]:
    """为每个工具的 execute 加上耗时日志和异常兜底。"""

    def wrap(t: ToolDef) -> ToolDef:
        orig = t.execute

        async def logged(args: dict[str, Any], deps, _orig=orig, _name=t.name):
            start = time.perf_counter()
            try:
                result = await _orig(args, deps)
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

        async def budgeted(args: dict[str, Any], deps, _orig=orig, _name=t.name):
            used = deps.usage_budget.get("calls", 0)
            if used >= limit:
                return tool_error(f"工具调用次数已达上限 ({used}/{limit})，请直接回答", "BUDGET")
            result = await _orig(args, deps)
            deps.usage_budget["calls"] = used + 1
            return result

        return dataclasses.replace(t, execute=budgeted)

    return [wrap(t) for t in tools]
