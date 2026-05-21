"""动态工具发现测试 — ToolRegistry、ToolDiscoveryState、tool_search。"""

from __future__ import annotations

import json

import pytest

from lib.tools._base import ToolDef, ToolMeta
from lib.tools._discovery import ToolDiscoveryState
from lib.tools._registry import ToolRegistry
from lib.tools._search_tool import _tool_search
from lib.tools.factory import (
    assemble_visible_tools,
    build_deferred_tools_hint,
    register_all_tools,
)


class FakeDeps:
    def __init__(self, conversation_id: str | None = None) -> None:
        self.conversation_id = conversation_id


@pytest.fixture(autouse=True)
def _reset_registry():
    from lib.tools._discovery import reset_tool_discovery_state
    from lib.tools._registry import reset_tool_registry

    reset_tool_registry()
    reset_tool_discovery_state()
    yield
    reset_tool_registry()
    reset_tool_discovery_state()


# ── ToolRegistry 测试 ───────────────────────────────────────────────


def test_registry_register_and_search() -> None:
    reg = ToolRegistry()
    reg.register(
        ToolDef(name="alpha", description="Alpha tool for testing", input_schema={}),
    )
    reg.register(
        ToolDef(
            name="beta",
            description="Beta search tool",
            input_schema={},
            meta=ToolMeta(search_hint="查找、搜索"),
        ),
    )

    assert reg.get_registered_names() == {"alpha", "beta"}

    results = reg.search("alpha")
    assert len(results) == 1
    assert results[0]["name"] == "alpha"

    results = reg.search("查找")
    assert len(results) == 1
    assert results[0]["name"] == "beta"

    results = reg.search("tool")
    assert len(results) == 2  # alpha 和 beta 的 description 都含 "tool"


def test_registry_search_excludes_meta_tools() -> None:
    reg = ToolRegistry()
    reg.register(ToolDef(name="tool_search", description="搜索工具", input_schema={}))
    reg.register(ToolDef(name="other", description="其他工具", input_schema={}))

    results = reg.search("工具")
    names = [r["name"] for r in results]
    assert "tool_search" not in names
    assert "other" in names


def test_registry_search_risk_filter() -> None:
    reg = ToolRegistry()
    reg.register(
        ToolDef(
            name="safe",
            description="Safe tool",
            input_schema={},
            meta=ToolMeta(risk="read-only"),
        ),
    )
    reg.register(
        ToolDef(
            name="danger",
            description="Dangerous tool",
            input_schema={},
            meta=ToolMeta(risk="destructive"),
        ),
    )

    results = reg.search("tool", allowed_risk=["read-only"])
    assert len(results) == 1
    assert results[0]["name"] == "safe"


def test_registry_get_deferred_names() -> None:
    reg = ToolRegistry()
    reg.register(
        ToolDef(name="always", description="Always on", input_schema={}, meta=ToolMeta(always_on=True)),
    )
    reg.register(ToolDef(name="deferred1", description="Deferred one", input_schema={}))
    reg.register(
        ToolDef(name="mcp_tool", description="MCP tool", input_schema={}),
        source_type="mcp",
        source_name="test_server",
    )

    deferred = reg.get_deferred_names(visible=set())
    assert "always" not in deferred["builtin"]
    assert "deferred1" in deferred["builtin"]
    assert "mcp_tool" in deferred["mcp"].get("test_server", [])


def test_registry_get_schemas() -> None:
    reg = ToolRegistry()
    reg.register(ToolDef(name="a", description="A", input_schema={"type": "object"}))
    schemas = reg.get_schemas({"a", "missing"})
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "a"


# ── ToolDiscoveryState 测试 ────────────────────────────────────────


def test_discovery_cold_start() -> None:
    d = ToolDiscoveryState()
    assert d.get_visible("conv-1") == []


def test_discovery_update_and_order() -> None:
    d = ToolDiscoveryState(capacity=3)
    d.update("conv-1", ["web_search", "shell"], always_on={"memory_search"})
    assert d.get_visible("conv-1") == ["web_search", "shell"]

    d.update("conv-1", ["file_read"], always_on={"memory_search"})
    assert d.get_visible("conv-1") == ["file_read", "web_search", "shell"]

    # 再次使用 web_search，它应移到最前
    d.update("conv-1", ["web_search"], always_on={"memory_search"})
    assert d.get_visible("conv-1") == ["web_search", "file_read", "shell"]


def test_discovery_eviction() -> None:
    d = ToolDiscoveryState(capacity=2)
    d.update("conv-1", ["a", "b", "c"], always_on=set())
    assert d.get_visible("conv-1") == ["a", "b"]  # c 被截断


def test_discovery_skips_always_on() -> None:
    d = ToolDiscoveryState()
    d.update("conv-1", ["memory_search"], always_on={"memory_search"})
    assert d.get_visible("conv-1") == []


def test_discovery_max_conversations() -> None:
    d = ToolDiscoveryState(max_conversations=2)
    d.update("conv-1", ["a"], always_on=set())
    d.update("conv-2", ["b"], always_on=set())
    d.update("conv-3", ["c"], always_on=set())
    assert d.get_visible("conv-1") == []  # 被淘汰
    assert d.get_visible("conv-3") == ["c"]


# ── factory 集成测试 ───────────────────────────────────────────────


def test_register_all_tools_and_visible() -> None:
    register_all_tools()
    visible = assemble_visible_tools("test-conv")
    names = [t.name for t in visible]
    # 冷启动时只有 always_on 工具
    assert "memory_search" in names
    assert "memory_save" in names
    assert "get_profile" in names
    assert "tool_search" in names
    # deferred 工具不可见
    assert "shell" not in names
    assert "web_search" not in names


def test_deferred_hint_format() -> None:
    register_all_tools()
    hint = build_deferred_tools_hint("test-conv")
    assert "可用但未加载的工具" in hint
    # tool_search 是 meta 工具，不应出现在 builtin 列表中
    # 但 hint 描述文本中可能提到 "tool_search"，所以只检查 builtin 行
    lines = hint.splitlines()
    builtin_line = next((ln for ln in lines if ln.startswith("builtin:")), "")
    assert "tool_search" not in builtin_line
    assert "shell" in hint or "builtin:" in hint


# ── tool_search 元工具测试 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_search_select_exact() -> None:
    register_all_tools()
    deps = FakeDeps(conversation_id="conv-select")
    result = await _tool_search({"query": "select:shell"}, deps)
    data = json.loads(result)
    assert any(r["name"] == "shell" for r in data["matched"])


@pytest.mark.asyncio
async def test_tool_search_keyword() -> None:
    register_all_tools()
    deps = FakeDeps(conversation_id="conv-keyword")
    result = await _tool_search({"query": "搜索网页"}, deps)
    data = json.loads(result)
    assert any(r["name"] == "web_search" for r in data["matched"])
    assert any(r["name"] == "web_search" for r in data["matched"])


@pytest.mark.asyncio
async def test_tool_search_already_loaded() -> None:
    register_all_tools()
    deps = FakeDeps(conversation_id="conv-loaded")
    # 先解锁
    await _tool_search({"query": "select:shell"}, deps)
    # 再次 select
    result = await _tool_search({"query": "select:shell"}, deps)
    data = json.loads(result)
    assert "shell" in data["already_loaded"]


@pytest.mark.asyncio
async def test_tool_search_risk_filter() -> None:
    register_all_tools()
    deps = FakeDeps(conversation_id="conv-risk")
    # 搜索 shell，但只允许 read-only
    result = await _tool_search({"query": "select:shell", "allowed_risk": ["read-only"]}, deps)
    data = json.loads(result)
    assert "shell" in data.get("tip", "")  # 风险等级不符提示
    assert not any(r["name"] == "shell" for r in data["matched"])


@pytest.mark.asyncio
async def test_tool_search_empty_query() -> None:
    register_all_tools()
    deps = FakeDeps(conversation_id="conv-empty")
    result = await _tool_search({"query": ""}, deps)
    assert "工具错误" in result


# ── 预加载流转测试 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unlocked_tool_visible_next_turn() -> None:
    """解锁的工具在下一轮对话中可见。"""
    register_all_tools()
    conv_id = "conv-flow"
    deps = FakeDeps(conversation_id=conv_id)

    # 第一轮：冷启动，shell 不可见
    visible1 = assemble_visible_tools(conv_id)
    assert "shell" not in [t.name for t in visible1]

    # 第一轮中调用 tool_search 解锁 shell
    result = await _tool_search({"query": "select:shell"}, deps)
    data = json.loads(result)
    assert any(r["name"] == "shell" for r in data["matched"])

    # 第二轮：shell 已预加载，可见
    visible2 = assemble_visible_tools(conv_id)
    assert "shell" in [t.name for t in visible2]
