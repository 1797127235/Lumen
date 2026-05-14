"""工具策略层单元测试 — PathPolicy / LoopGuardPolicy / BudgetPolicy / ApprovalPolicy。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.modules.agent.tools.core.context import ToolRuntimeContext
from backend.modules.agent.tools.core.definitions import ToolDefinition
from backend.modules.agent.tools.core.policies import (
    ApprovalPolicy,
    BudgetPolicy,
    LoopGuardPolicy,
    PathPolicy,
    ResultPolicy,
)

# ── PathPolicy ──


class TestPathPolicy:
    """路径策略测试。"""

    @pytest.fixture
    def ctx(self, tmp_path: Path) -> ToolRuntimeContext:
        return ToolRuntimeContext(
            user_id="test_user",
            workspace_root=tmp_path,
            cwd=tmp_path,
        )

    def test_resolve_relative_path(self, ctx: ToolRuntimeContext, tmp_path: Path) -> None:
        """相对路径应基于 cwd 解析。"""
        resolved, err = PathPolicy.resolve("foo.txt", ctx)
        assert err == ""
        assert resolved == tmp_path / "foo.txt"

    def test_resolve_absolute_path(self, ctx: ToolRuntimeContext, tmp_path: Path) -> None:
        """绝对路径应在 workspace_root 内。"""
        resolved, err = PathPolicy.resolve(str(tmp_path / "sub" / "file.txt"), ctx)
        assert err == ""
        assert resolved == tmp_path / "sub" / "file.txt"

    def test_resolve_path_escape(self, ctx: ToolRuntimeContext, tmp_path: Path) -> None:
        """路径越界应被拒绝。"""
        resolved, err = PathPolicy.resolve("../escape.txt", ctx)
        assert resolved is None
        assert "越界" in err

    def test_resolve_sensitive_file(self, ctx: ToolRuntimeContext) -> None:
        """敏感文件名应被拒绝。"""
        resolved, err = PathPolicy.resolve(".env", ctx)
        assert resolved is None
        assert "敏感" in err

    def test_resolve_empty_path(self, ctx: ToolRuntimeContext) -> None:
        """空路径应报错。"""
        resolved, err = PathPolicy.resolve("", ctx)
        assert resolved is None
        assert "不能为空" in err

    def test_resolve_no_workspace(self) -> None:
        """workspace_root 缺失时应报配置错误。"""
        ctx = ToolRuntimeContext(user_id="test_user", workspace_root=None)
        resolved, err = PathPolicy.resolve("foo.txt", ctx)
        assert resolved is None
        assert "workspace_root" in err


# ── LoopGuardPolicy ──


class TestLoopGuardPolicy:
    """循环保护策略测试。"""

    @pytest.fixture
    def tool(self) -> ToolDefinition:
        return ToolDefinition(
            name="file_read",
            description="读取文件",
            handler=None,
        )

    @pytest.fixture
    def ctx(self) -> ToolRuntimeContext:
        return ToolRuntimeContext(user_id="test_user")

    def test_check_empty_history(self, tool: ToolDefinition, ctx: ToolRuntimeContext) -> None:
        """无历史调用时应允许。"""
        ok, msg = LoopGuardPolicy.check(tool, ctx, {"path": "foo.txt"})
        assert ok is True
        assert msg == ""

    def test_consecutive_fails_threshold(self, tool: ToolDefinition, ctx: ToolRuntimeContext) -> None:
        """同一工具+同路径连续失败 2 次应触发阻断。"""
        # 模拟 2 次失败
        LoopGuardPolicy.record(tool, ctx, {"path": "foo.txt"}, ok=False)
        LoopGuardPolicy.record(tool, ctx, {"path": "foo.txt"}, ok=False)

        ok, msg = LoopGuardPolicy.check(tool, ctx, {"path": "foo.txt"})
        assert ok is False
        assert "已连续失败" in msg

    def test_consecutive_fails_different_path(self, tool: ToolDefinition, ctx: ToolRuntimeContext) -> None:
        """不同路径的失败不计入同一循环。"""
        LoopGuardPolicy.record(tool, ctx, {"path": "foo.txt"}, ok=False)
        LoopGuardPolicy.record(tool, ctx, {"path": "foo.txt"}, ok=False)

        ok, _msg = LoopGuardPolicy.check(tool, ctx, {"path": "bar.txt"})
        assert ok is True

    def test_exploration_limit(self, ctx: ToolRuntimeContext) -> None:
        """连续 6 次文件工具调用应触发阻断。"""
        tools = [
            ToolDefinition(name="file_read", description="读取文件", handler=None),
            ToolDefinition(name="file_list", description="列出文件", handler=None),
            ToolDefinition(name="file_search", description="搜索文件", handler=None),
        ]
        # 6 次文件工具调用
        for i in range(6):
            t = tools[i % len(tools)]
            LoopGuardPolicy.record(t, ctx, {"path": f"path{i}"}, ok=True)

        ok, msg = LoopGuardPolicy.check(tools[0], ctx, {"path": "path6"})
        assert ok is False
        assert "多次文件操作" in msg

    def test_exploration_resets_on_non_file_tool(self, ctx: ToolRuntimeContext) -> None:
        """非文件工具调用应重置探索计数。"""
        file_tool = ToolDefinition(name="file_read", description="读取文件", handler=None)
        mem_tool = ToolDefinition(name="memory_search", description="搜索记忆", handler=None)

        # 5 次文件工具
        for i in range(5):
            LoopGuardPolicy.record(file_tool, ctx, {"path": f"path{i}"}, ok=True)
        # 1 次非文件工具
        LoopGuardPolicy.record(mem_tool, ctx, {"query": "test"}, ok=True)
        # 再来 1 次文件工具应允许
        ok, _msg = LoopGuardPolicy.check(file_tool, ctx, {"path": "path5"})
        assert ok is True

    def test_history_truncation(self, ctx: ToolRuntimeContext) -> None:
        """历史记录超过 50 条时应截断，只保留较新的记录。"""
        tool = ToolDefinition(name="file_read", description="读取文件", handler=None)
        for i in range(55):
            LoopGuardPolicy.record(tool, ctx, {"path": f"path{i}"}, ok=True)

        calls = ctx.tool_state.get("_tool_calls", [])
        # 超过 50 条时截断为最后 25 条，但后续追加会继续增长
        paths = [c["path"] for c in calls]
        # 旧记录（path0-25）应已被截断丢弃
        assert "path0" not in paths
        assert "path25" not in paths
        # 新记录应保留
        assert "path26" in paths
        assert "path54" in paths


# ── BudgetPolicy ──


class TestBudgetPolicy:
    """预算策略测试。"""

    @pytest.fixture
    def tool(self) -> ToolDefinition:
        return ToolDefinition(name="file_read", description="读取文件", handler=None)

    @pytest.fixture
    def ctx(self) -> ToolRuntimeContext:
        return ToolRuntimeContext(user_id="test_user")

    def test_check_within_budget(self, ctx: ToolRuntimeContext) -> None:
        """预算内应允许。"""
        tool = ToolDefinition(name="file_read", description="读取文件", handler=None)
        ok, msg = BudgetPolicy.check(tool, ctx)
        assert ok is True
        assert msg == ""

    def test_check_budget_exceeded(self, ctx: ToolRuntimeContext) -> None:
        """预算耗尽应拒绝。"""
        tool = ToolDefinition(name="file_read", description="读取文件", handler=None)
        ctx.usage_budget["tool_calls"] = 6
        ctx.usage_budget["tool_calls_limit"] = 6
        ok, msg = BudgetPolicy.check(tool, ctx)
        assert ok is False
        assert "已达上限" in msg

    def test_consume_increments(self, ctx: ToolRuntimeContext) -> None:
        """consume 应递增计数。"""
        assert ctx.usage_budget.get("tool_calls", 0) == 0
        BudgetPolicy.consume(ctx)
        assert ctx.usage_budget["tool_calls"] == 1
        BudgetPolicy.consume(ctx)
        assert ctx.usage_budget["tool_calls"] == 2


# ── ApprovalPolicy ──


class TestApprovalPolicy:
    """审批策略测试。"""

    def test_read_tool_no_approval(self) -> None:
        """只读工具不需要审批。"""
        tool = ToolDefinition(name="file_read", description="读取文件", read_only=True)
        needs, _reason = ApprovalPolicy.check(tool)
        assert needs is False

    def test_write_tool_needs_approval(self) -> None:
        """写工具标记 requires_approval 时需要审批。"""
        tool = ToolDefinition(name="file_write", description="写入文件", read_only=False, requires_approval=True)
        needs, reason = ApprovalPolicy.check(tool)
        assert needs is True
        assert "file_write" in reason


# ── ResultPolicy ──


class TestResultPolicy:
    """结果策略测试。"""

    def test_format_error_with_code(self) -> None:
        """带 code 的错误格式。"""
        msg = ResultPolicy.format_error("something wrong", "TEST_CODE")
        assert "[工具错误/TEST_CODE]" in msg
        assert "something wrong" in msg

    def test_format_error_without_code(self) -> None:
        """不带 code 的错误格式。"""
        msg = ResultPolicy.format_error("plain error")
        assert msg == "[工具错误] plain error"

    def test_format_success(self) -> None:
        """成功结果应原样返回。"""
        data = "hello world"
        result = ResultPolicy.format_success(data)
        assert result == data
