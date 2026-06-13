"""tool_search 元工具 — Agent 用它来发现其他工具。"""

from __future__ import annotations

import json
from typing import Any

from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok
from lib.tools._discovery import get_tool_discovery_state
from lib.tools._registry import get_tool_registry
from shared.logging import get_logger

logger = get_logger(__name__)


async def _tool_search(args: dict[str, Any], ctx: Any = None):
    query: str = args.get("query", "").strip()
    top_k: int = min(int(args.get("top_k", 5)), 10)
    allowed_risk: list[str] | None = args.get("allowed_risk")

    if not query:
        return tool_error("query 不能为空，请描述你需要的功能")

    registry = get_tool_registry()
    discovery = get_tool_discovery_state()
    conversation_id = args.get("conversation_id")
    if conversation_id is None and ctx is not None:
        deps = getattr(ctx, "deps", ctx)
        conversation_id = getattr(deps, "conversation_id", None)

    always_on = registry.get_always_on_names()
    preloaded = set(discovery.get_visible(conversation_id))
    excluded = always_on | preloaded | {"tool_search"}

    # ── select: 精确加载路径 ──────────────────────────────────────
    if query.lower().startswith("select:"):
        return _handle_select(
            query[7:],
            registry,
            excluded,
            allowed_risk,
            discovery,
            conversation_id,
            always_on,
        )

    # ── 关键词搜索路径 ──────────────────────────────────────────────
    results = registry.search(
        query=query,
        top_k=top_k,
        allowed_risk=allowed_risk,
        excluded_names=excluded,
    )
    if not results:
        return tool_ok(
            json.dumps(
                {
                    "matched": [],
                    "tip": "未找到匹配工具，请换个关键词重试",
                },
                ensure_ascii=False,
            )
        )

    unlocked = [r["name"] for r in results]
    discovery.update(conversation_id, unlocked, always_on)
    logger.info("tool_search unlocked", conversation_id=conversation_id, unlocked=unlocked)

    return tool_ok(
        json.dumps(
            {"matched": results},
            ensure_ascii=False,
        )
    )


def _handle_select(
    names_str: str,
    registry,
    excluded: set[str],
    allowed_risk: list[str] | None,
    discovery,
    conversation_id: str | None,
    always_on: set[str],
):
    """处理 select:A,B,C 精确加载路径。"""
    requested = [n.strip() for n in names_str.split(",") if n.strip()]
    if not requested:
        return tool_ok(
            json.dumps(
                {
                    "matched": [],
                    "tip": "select: 后面需要提供工具名",
                },
                ensure_ascii=False,
            )
        )

    risk_filter = set(allowed_risk) if allowed_risk else None

    already_loaded: list[str] = []
    found: list[str] = []
    missing: list[str] = []
    risk_blocked: list[str] = []

    for name in requested:
        if name in excluded:
            already_loaded.append(name)
        elif not registry.has_tool(name):
            missing.append(name)
        else:
            doc = registry.get_tool(name)
            if doc and risk_filter and doc.meta.risk not in risk_filter:
                risk_blocked.append(name)
            else:
                found.append(name)

    if found:
        discovery.update(conversation_id, found, always_on)
        logger.info("tool_search select unlocked", conversation_id=conversation_id, found=found)

    matched = registry.get_schemas_as_doc_results(found)
    result: dict[str, Any] = {
        "matched": matched,
        "already_loaded": already_loaded,
    }

    tip_parts: list[str] = []
    if already_loaded:
        tip_parts.append(f"已加载可直接调用: {', '.join(already_loaded)}")
    if missing:
        tip_parts.append(f"未找到工具: {', '.join(missing)}，请用关键词搜索确认正确名称")
    if risk_blocked:
        tip_parts.append(f"风险等级不符（allowed_risk={allowed_risk}）: {', '.join(risk_blocked)}")
    if tip_parts:
        result["tip"] = "; ".join(tip_parts)

    return tool_ok(json.dumps(result, ensure_ascii=False))


def create_tool_search() -> ToolDef:
    return ToolDef(
        name="tool_search",
        description=(
            "在工具目录中搜索可用工具。找到的工具在当前 run 的下一步立即可用。\n\n"
            "调用时机：\n"
            "- 需要某类功能，但不知道工具名称 → 必须调用\n"
            "- 知道工具名且已可见 → 直接调用，不要先搜索\n"
            "- 知道工具名但不可见 → 用 select: 前缀精确加载（见下）\n"
            "- 收到'工具不存在'错误 → 必须调用，用错误中的建议关键词搜索\n"
            "- 纯对话/推理，不涉及工具能力 → 不调用\n\n"
            "查询形式：\n"
            '- "select:工具名" → 精确加载已知工具，支持逗号分隔多个："select:A,B,C"\n'
            '- "关键词" → 模糊搜索，例如："定时提醒"、"文件读取"、"搜索网页"\n\n'
            "正确流程：调用 tool_search → 查看结果 → 下一步直接调用找到的工具"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "搜索查询。两种形式：\n"
                        '1. "select:工具名" 精确加载（支持逗号分隔多个）\n'
                        '2. 关键词描述功能，例如："定时任务"、"文件读取"、"搜索网页"'
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": "关键词搜索时返回的最大工具数量，默认 5，最大 10",
                    "default": 5,
                },
                "allowed_risk": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["read-only", "write", "destructive"],
                    },
                    "description": (
                        "允许的风险等级，不填则不过滤。read-only=只读，write=写操作，destructive=破坏性操作"
                    ),
                },
            },
            "required": ["query"],
        },
        execute=_tool_search,
        read_only=True,
        meta=ToolMeta(
            always_on=True,
            risk="read-only",
            search_hint="查找工具、加载工具、发现工具、tool search",
        ),
    )
