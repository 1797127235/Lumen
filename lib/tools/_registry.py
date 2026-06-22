"""工具注册表 — 管理全量工具索引，提供搜索和按名称过滤 schema 能力。"""

from __future__ import annotations

import time
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast

from lib.metrics import record
from lib.tools._base import ToolDef
from shared.logging import get_logger

logger = get_logger(__name__)

# 元工具（不参与搜索结果，也不出现在 deferred 工具目录里）
_META_TOOLS: frozenset[str] = frozenset({"tool_search"})

_PROGRESS_DESCRIPTION_FIELD = "description"
_PROGRESS_DESCRIPTION_SCHEMA: dict[str, str] = {
    "type": "string",
    "description": (
        "用 5-12 个字说明这次工具调用的意图，只写给用户看的短语。"
        "不要复述工具名，不要粘贴长参数。例如：查看目录、读取配置、搜索健康数据。"
    ),
}


def _schema_properties(parameters: dict[str, Any]) -> dict[str, Any]:
    raw_properties = parameters.get("properties")
    if isinstance(raw_properties, dict):
        return cast(dict[str, Any], raw_properties)
    properties: dict[str, Any] = {}
    parameters["properties"] = properties
    return properties


def _tool_defines_parameter(tool: ToolDef, name: str) -> bool:
    parameters: dict[str, Any] = tool.input_schema or {}
    properties = parameters.get("properties")
    return isinstance(properties, dict) and name in properties


def _with_progress_description(schema: dict[str, Any], tool: ToolDef) -> dict[str, Any]:
    """为 schema 注入 description 参数字段（如果工具本身未定义）。"""
    cloned = cast(dict[str, Any], deepcopy(schema))
    function = cloned.get("function")
    if not isinstance(function, dict):
        return cloned
    function = cast(dict[str, Any], function)
    parameters = function.get("parameters")
    if not isinstance(parameters, dict):
        return cloned
    parameters = cast(dict[str, Any], parameters)
    if _tool_defines_parameter(tool, _PROGRESS_DESCRIPTION_FIELD):
        return cloned
    properties = _schema_properties(parameters)
    properties[_PROGRESS_DESCRIPTION_FIELD] = dict(_PROGRESS_DESCRIPTION_SCHEMA)
    required = parameters.get("required")
    if isinstance(required, list):
        if _PROGRESS_DESCRIPTION_FIELD not in required:
            cast(list[Any], required).append(_PROGRESS_DESCRIPTION_FIELD)
    else:
        parameters["required"] = [_PROGRESS_DESCRIPTION_FIELD]
    return cloned


@dataclass
class ToolDocument:
    """工具的索引态视图，供搜索后端使用。"""

    name: str
    description: str
    risk: str
    always_on: bool
    search_hint: str | None
    tags: list[str]
    source_type: str  # "builtin" | "mcp"
    source_name: str  # mcp server 名，builtin 为空字符串

    @classmethod
    def from_tool_and_meta(
        cls,
        tool: ToolDef,
        source_type: str = "builtin",
        source_name: str = "",
    ) -> ToolDocument:
        return cls(
            name=tool.name,
            description=tool.description,
            risk=tool.meta.risk,
            always_on=tool.meta.always_on,
            search_hint=tool.meta.search_hint,
            tags=tool.meta.tags,
            source_type=source_type,
            source_name=source_name,
        )


class ToolRegistry:
    """管理所有可用工具。全局单例。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}
        self._documents: dict[str, ToolDocument] = {}
        self._context: dict[str, Any] = {}

    def set_context(self, **kwargs: Any) -> None:
        """设置当前会话上下文，execute 时自动合并进 kwargs。"""
        self._context.update(kwargs)

    async def execute(self, name: str, arguments: dict[str, Any], ctx: Any = None) -> str:
        """执行工具，自动合总会话上下文。"""
        tool = self._tools.get(name)
        if tool is None:
            await record("tool.calls", 1.0, labels={"tool_name": name, "status": "not_found"})
            return f"❌ 工具 '{name}' 不存在"
        if tool.execute is None:
            await record("tool.calls", 1.0, labels={"tool_name": name, "status": "no_executor"})
            return f"❌ 工具 '{name}' 没有执行函数"

        merged: dict[str, Any] = {**self._context, **arguments}
        if not _tool_defines_parameter(tool, _PROGRESS_DESCRIPTION_FIELD):
            merged.pop(_PROGRESS_DESCRIPTION_FIELD, None)

        # 工具执行计时：埋在此处同时覆盖主 agent（_execute_tool）和 worker agent（run_worker_agent）
        started = time.perf_counter()
        status = "ok"
        try:
            result = await tool.execute(merged, ctx)
            return str(result)
        except Exception as exc:
            status = "error"
            logger.exception("工具执行出错", tool=name)
            return f"❌ 执行失败: {exc}"
        finally:
            try:
                duration_ms = (time.perf_counter() - started) * 1000
                await record(
                    "tool.duration_ms",
                    duration_ms,
                    labels={"tool_name": name, "status": status},
                )
                await record("tool.calls", 1.0, labels={"tool_name": name, "status": status})
            except Exception:
                pass

    def register(
        self,
        tool: ToolDef,
        source_type: str = "builtin",
        source_name: str = "",
    ) -> None:
        self._tools[tool.name] = tool
        doc = ToolDocument.from_tool_and_meta(tool, source_type=source_type, source_name=source_name)
        self._documents[tool.name] = doc
        logger.debug("工具注册", name=tool.name, always_on=tool.meta.always_on, risk=tool.meta.risk)

    def unregister(self, name: str) -> None:
        removed = self._tools.pop(name, None)
        self._documents.pop(name, None)
        if removed:
            logger.debug("工具注销", name=name)

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def get_tool(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def get_registered_names(self) -> set[str]:
        return set(self._tools.keys())

    def get_schemas(self, names: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """按名称集合过滤返回 OpenAI function calling 格式的工具定义。"""
        schemas: list[dict[str, Any]] = []
        if names is None:
            ordered_names = list(self._tools.keys())
        elif isinstance(names, set | frozenset):
            ordered_names = [name for name in self._tools if name in names]
        else:
            ordered_names = list(names)

        for name in ordered_names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            schema = self._tool_to_schema(tool)
            schemas.append(_with_progress_description(schema, tool))
        return schemas

    def get_registered_order(self, names: set[str] | None = None) -> list[str]:
        if names is None:
            return list(self._tools.keys())
        return [name for name in self._tools if name in names]

    def get_always_on_names(self) -> set[str]:
        return {name for name, doc in self._documents.items() if doc.always_on}

    def get_documents(self) -> list[ToolDocument]:
        return list(self._documents.values())

    def get_deferred_tools(self, visible: set[str] | None = None) -> dict[str, Any]:
        """返回所有 deferred 工具，按来源分组。

        visible: 当前已可见工具名（always_on + preloaded），从结果中排除。
        deferred = 全量注册工具 - always_on - meta_tools - visible

        返回:
            {
                "builtin": [(name, description), ...],
                "mcp": {server_name: [(name, description), ...]}
            }
        """
        always_on = self.get_always_on_names()
        excluded = always_on | _META_TOOLS | (visible or set())

        builtin: list[tuple[str, str]] = []
        mcp: dict[str, list[tuple[str, str]]] = {}

        for name, doc in self._documents.items():
            if name in excluded:
                continue
            desc = doc.description.split("\n")[0][:80]  # 取第一行，截断到 80 字符
            if doc.source_type == "mcp":
                mcp.setdefault(doc.source_name, []).append((name, desc))
            else:
                builtin.append((name, desc))

        builtin.sort(key=lambda x: x[0])
        mcp = {k: sorted(v, key=lambda x: x[0]) for k, v in sorted(mcp.items())}

        return {
            "builtin": builtin,
            "mcp": mcp,
        }

    def search(
        self,
        query: str,
        top_k: int = 5,
        allowed_risk: list[str] | None = None,
        excluded_names: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """关键词搜索工具目录，返回匹配的工具信息列表。

        excluded_names: 调用方传入的排除集合，通常为已可见工具名。
        meta_tools 始终被排除。
        """
        excluded = _META_TOOLS | (excluded_names or set())
        risk_filter = set(allowed_risk) if allowed_risk else None
        query_lower = query.lower()
        scores: list[tuple[int, dict[str, Any]]] = []

        for name, doc in self._documents.items():
            if name in excluded:
                continue
            if risk_filter and doc.risk not in risk_filter:
                continue

            score = 0
            if query_lower in name.lower():
                score += 10
            if query_lower in doc.description.lower():
                score += 5
            if doc.search_hint and query_lower in doc.search_hint.lower():
                score += 3
            for tag in doc.tags:
                if query_lower in tag.lower():
                    score += 2

            if score > 0:
                scores.append(
                    (
                        score,
                        {
                            "name": doc.name,
                            "summary": doc.description[:120],
                            "why_matched": ["关键词匹配"],
                            "risk": doc.risk,
                            "always_on": doc.always_on,
                        },
                    )
                )

        scores.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scores[:top_k]]

    def get_schemas_as_doc_results(self, names: list[str]) -> list[dict[str, Any]]:
        """将工具名列表转为与 search() 相同格式的结果列表。"""
        results: list[dict[str, Any]] = []
        for name in names:
            doc = self._documents.get(name)
            if doc:
                results.append(
                    {
                        "name": doc.name,
                        "summary": doc.description[:120],
                        "why_matched": ["名称:精确匹配"],
                        "risk": doc.risk,
                        "always_on": doc.always_on,
                    }
                )
        return results

    def get_mcp_server_names(self) -> set[str]:
        return {doc.source_name for doc in self._documents.values() if doc.source_type == "mcp"}

    def get_tool_names_by_source(self, source_type: str, source_name: str) -> set[str]:
        return {
            name
            for name, doc in self._documents.items()
            if doc.source_type == source_type and doc.source_name == source_name
        }

    # ── 内部方法 ────────────────────────────────────────────────────

    @staticmethod
    def _tool_to_schema(tool: ToolDef) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }


# 模块级单例
_TOOL_REGISTRY: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _TOOL_REGISTRY
    if _TOOL_REGISTRY is None:
        _TOOL_REGISTRY = ToolRegistry()
    return _TOOL_REGISTRY


def reset_tool_registry() -> None:
    global _TOOL_REGISTRY
    _TOOL_REGISTRY = ToolRegistry()
