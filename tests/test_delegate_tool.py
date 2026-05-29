"""tests/test_delegate_tool.py — delegate 子 Agent 工具测试。

设计文档第 8 节测试计划：
- bundle 解析
- 黑名单生效
- 默认 toolsets=None
- child 抛错时 handler 返回错误摘要
- child message_history 为空
- progress_emitter=None 时不报错
- mock model 最小 delegate 集成测试
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.tools.delegate import (
    _BLOCKLIST,
    _BUNDLES,
    _handle_delegate,
    create_delegate_tools,
    resolve_tool_names,
)

# ═══════════════════════════════════════════════════════════════
#  1. Bundle 解析
# ═══════════════════════════════════════════════════════════════


class TestResolveToolNames:
    def test_web_files_bundles_expand(self):
        """["web","files"] → 展开成 8 个工具名"""
        result = resolve_tool_names(["web", "files"])
        expected = [
            "web_search",
            "web_extract",
            "web_crawl",
            "file_read",
            "file_write",
            "file_ls",
            "file_grep",
            "file_edit",
        ]
        assert result == expected

    def test_web_only(self):
        result = resolve_tool_names(["web"])
        assert result == ["web_search", "web_extract", "web_crawl"]

    def test_files_only(self):
        result = resolve_tool_names(["files"])
        assert result == ["file_read", "file_write", "file_ls", "file_grep", "file_edit"]

    def test_default_is_web_files(self):
        """默认 toolsets=None → 等价于 ["web","files"]"""
        result = resolve_tool_names(None)
        expected = resolve_tool_names(["web", "files"])
        assert result == expected

    def test_unknown_bundle_ignored(self):
        """未知 bundle 名被忽略不报错"""
        result = resolve_tool_names(["web", "nonexistent", "files"])
        expected = resolve_tool_names(["web", "files"])
        assert result == expected

    def test_empty_list(self):
        """空列表返回空"""
        result = resolve_tool_names([])
        assert result == []


# ═══════════════════════════════════════════════════════════════
#  2. 黑名单
# ═══════════════════════════════════════════════════════════════


class TestBlocklist:
    def test_blocklist_contains_memory_and_delegate(self):
        """黑名单含 memory / delegate 工具（实际注册名）"""
        assert "memory" in _BLOCKLIST
        assert "memory_search" in _BLOCKLIST
        assert "delegate" in _BLOCKLIST

    def test_blocklist_not_stale_names(self):
        """旧名 memory_save / tellAI 不应出现在黑名单中"""
        assert "memory_save" not in _BLOCKLIST
        assert "tellAI" not in _BLOCKLIST

    def test_resolved_tools_never_contain_blocklisted(self):
        """resolved 工具集永不含 memory / delegate"""
        result = resolve_tool_names(["web", "files"])
        for name in _BLOCKLIST:
            assert name not in result, f"{name} should be blocklisted"

    def test_delegate_not_in_any_bundle(self):
        """delegate 不在任何 bundle 里（双保险）"""
        for bundle_tools in _BUNDLES.values():
            assert "delegate" not in bundle_tools


# ═══════════════════════════════════════════════════════════════
#  3. Tool registration
# ═══════════════════════════════════════════════════════════════


class TestToolRegistration:
    def test_create_delegate_tools_returns_one_tool(self):
        tools = create_delegate_tools()
        assert len(tools) == 1
        assert tools[0].name == "delegate"

    def test_delegate_tool_meta(self):
        tools = create_delegate_tools()
        meta = tools[0].meta
        assert meta.always_on is True  # 常驻可见，调研类任务直接可委派
        assert meta.risk == "write"

    def test_delegate_schema_has_goal_required(self):
        tools = create_delegate_tools()
        schema = tools[0].input_schema
        assert "goal" in schema.get("required", [])
        assert "context" not in schema.get("required", [])
        assert "toolsets" not in schema.get("required", [])


# ═══════════════════════════════════════════════════════════════
#  4. progress_emitter=None 时 handler 不报错
# ═══════════════════════════════════════════════════════════════


class TestProgressEmitterNone:
    @pytest.mark.asyncio
    async def test_emit_progress_with_none_emitter(self):
        """_emit_progress_from_event 在 emit=None 时不报错"""
        from lib.tools.delegate import _emit_progress_from_event

        event = MagicMock()
        event.event_kind = "function_tool_call"
        event.tool_name = "web_search"
        event.part = MagicMock(args={"query": "test"}, tool_name="web_search")
        # Should not raise
        _emit_progress_from_event(event, None)

    @pytest.mark.asyncio
    async def test_emit_progress_captures_tool_call(self):
        """_emit_progress_from_event 正确捕获 function_tool_call 事件"""
        from lib.tools.delegate import _emit_progress_from_event

        captured: list[tuple[str, str]] = []

        def emit(phase: str, detail: str):
            captured.append((phase, detail))

        event = MagicMock(spec=["event_kind", "tool_name", "part"])
        event.event_kind = "function_tool_call"
        event.tool_name = "web_search"
        event.part = MagicMock()
        event.part.tool_name = "web_search"
        event.part.args = {"query": "向量数据库对比"}

        _emit_progress_from_event(event, emit)

        assert len(captured) == 1
        assert captured[0][0] == "step"
        assert "搜索" in captured[0][1]
        assert "向量数据库对比" in captured[0][1]

    @pytest.mark.asyncio
    async def test_emit_progress_handles_json_string_args(self):
        """args 为 JSON 字符串时不应抛 'str' object has no attribute 'get'"""
        from lib.tools.delegate import _emit_progress_from_event

        captured: list[tuple[str, str]] = []

        def emit(phase: str, detail: str):
            captured.append((phase, detail))

        event = MagicMock(spec=["event_kind", "tool_name", "part"])
        event.event_kind = "function_tool_call"
        event.tool_name = "web_search"
        event.part = MagicMock()
        event.part.tool_name = "web_search"
        event.part.args = '{"query": "前端开发趋势"}'  # JSON 字符串而非 dict

        _emit_progress_from_event(event, emit)

        assert len(captured) == 1
        assert "前端开发趋势" in captured[0][1]

    @pytest.mark.asyncio
    async def test_emit_progress_captures_tool_result(self):
        """_emit_progress_from_event 正确捕获 function_tool_result 事件"""
        from lib.tools.delegate import _emit_progress_from_event

        captured: list[tuple[str, str]] = []

        def emit(phase: str, detail: str):
            captured.append((phase, detail))

        event = MagicMock()
        event.event_kind = "function_tool_result"
        event.tool_name = ""
        event.part = MagicMock(tool_name="web_search")

        event.content = "搜索结果若干条"

        _emit_progress_from_event(event, emit)

        assert len(captured) == 1
        assert captured[0][0] == "step"
        assert "完成" in captured[0][1]

    @pytest.mark.asyncio
    async def test_emit_progress_reports_tool_error_result(self):
        """子 tool 返回 tool_error（❌ 前缀）时进度应报「失败」而非「完成」"""
        from lib.tools.delegate import _emit_progress_from_event

        captured: list[tuple[str, str]] = []

        def emit(phase: str, detail: str):
            captured.append((phase, detail))

        event = MagicMock(spec=["event_kind", "tool_name", "part", "content"])
        event.event_kind = "function_tool_result"
        event.tool_name = "web_search"
        event.part = MagicMock(tool_name="web_search")
        event.content = "❌ 搜索服务超时"

        _emit_progress_from_event(event, emit)

        assert len(captured) == 1
        assert "失败" in captured[0][1]
        assert "完成" not in captured[0][1]


# ═══════════════════════════════════════════════════════════════
#  5. Handler 错误隔离
# ═══════════════════════════════════════════════════════════════


class TestHandlerErrorIsolation:
    @pytest.mark.asyncio
    async def test_child_error_returns_error_summary(self):
        """child 抛错时 handler 返回错误摘要，不向上抛"""
        ctx = MagicMock()
        ctx.user_id = "test_user"
        ctx.workspace_root = "/tmp"
        ctx.progress_emitter = None

        with patch("core.agent.build_worker_agent") as mock_build:
            # 模拟 build_worker_agent 抛错
            mock_build.side_effect = RuntimeError("model config missing")

            result = await _handle_delegate(
                {"goal": "test task", "context": "", "toolsets": ["web"]},
                ctx,
            )
            # 结果应该包含错误信息
            assert "❌" in str(result.return_value)
            assert "失败" in str(result.return_value) or "error" in str(result.return_value).lower()


# ═══════════════════════════════════════════════════════════════
#  6. 集成测试：mock model 最小 delegate
# ═══════════════════════════════════════════════════════════════


class TestMinimalDelegateIntegration:
    @pytest.mark.asyncio
    async def test_mock_delegate_returns_summary_and_emits_progress(self):
        """mock model 跑最小 delegate：goal → child agent_run_result 事件 → 验证父拿到摘要"""
        progress_events: list[tuple[str, str]] = []

        def mock_emit(phase: str, detail: str):
            progress_events.append((phase, detail))

        ctx = MagicMock()
        ctx.user_id = "test_user"
        ctx.workspace_root = "/tmp"
        ctx.progress_emitter = mock_emit

        # Mock the worker agent's run_stream_events — 产出 agent_run_result 事件
        child_output = "调研结果摘要：这是 child Agent 的最终输出"

        class MockResult:
            output = child_output

        class MockRunResultEvent:
            event_kind = "agent_run_result"
            result = MockResult()

        class MockStreamCtx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def __aiter__(self):
                yield MockRunResultEvent()

        mock_worker = MagicMock()
        mock_worker.run_stream_events = MagicMock(return_value=MockStreamCtx())

        # Mock session maker: returns an async context manager
        mock_session = AsyncMock()

        class MockSessionMaker:
            def __call__(self):
                return self

            async def __aenter__(self):
                return mock_session

            async def __aexit__(self, *args):
                pass

        # Mock registry with registered tools
        mock_reg = MagicMock()
        mock_reg.get_registered_names.return_value = {"web_search"}

        with (
            patch("core.agent.build_worker_agent", return_value=mock_worker),
            patch("core.db.get_async_session_maker", return_value=MockSessionMaker()),
            patch("lib.tools._registry.get_tool_registry", return_value=mock_reg),
        ):
            result = await _handle_delegate(
                {"goal": "调研国内开源向量数据库现状", "context": "用户对向量数据库感兴趣", "toolsets": ["web"]},
                ctx,
            )

            # 父拿到摘要（从 agent_run_result 事件获取）
            assert "调研结果摘要" in result.return_value

            # SubagentProgress 被收集
            assert len(progress_events) > 0
            phases = [p[0] for p in progress_events]
            assert "started" in phases
            assert "done" in phases
