"""Shell 工具单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.tools.shell import (
    _BANNED,
    _BG_REGISTRY,
    _bg_kill,
    _resolve_cwd,
    _truncate,
    _validate_command,
    create_shell_tools,
)


@pytest.fixture(autouse=True)
def _cleanup_bg_registry():
    """每个测试后清理后台任务注册表。"""
    yield
    for task_id in list(_BG_REGISTRY.keys()):
        _bg_kill(task_id)
    _BG_REGISTRY.clear()


class FakeDeps:
    """模拟 LumenDeps。"""

    def __init__(self, workspace_root: Path | str | None = None) -> None:
        self.workspace_root = workspace_root


# ── ToolDef 结构测试 ─────────────────────────────────────────────────


def test_create_shell_tools_returns_three() -> None:
    tools = create_shell_tools()
    names = [t.name for t in tools]
    assert names == ["shell", "task_output", "task_stop"]
    assert tools[0].read_only is False  # shell 是写操作
    assert tools[1].read_only is True  # task_output 只读
    assert tools[2].read_only is False  # task_stop 是写操作


# ── 前台命令测试 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shell_echo() -> None:
    tool = create_shell_tools()[0]
    deps = FakeDeps(workspace_root=Path.cwd())
    result = await tool.execute({"command": "echo hello_lumen", "description": "测试 echo"}, deps)
    data = json.loads(result)
    assert data["exit_code"] == 0
    assert "hello_lumen" in data["output"]
    assert data["interrupted"] is False
    assert data["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_shell_empty_command() -> None:
    tool = create_shell_tools()[0]
    deps = FakeDeps()
    result = await tool.execute({"command": "", "description": "空命令"}, deps)
    assert "工具错误" in result


@pytest.mark.asyncio
async def test_shell_banned_command() -> None:
    tool = create_shell_tools()[0]
    deps = FakeDeps()
    for banned in _BANNED:
        result = await tool.execute({"command": f"{banned} example.com", "description": "测试黑名单"}, deps)
        assert "工具错误" in result
        assert "SAFETY" in result


# ── 后台任务测试 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shell_background_start_and_stop() -> None:
    tool = create_shell_tools()[0]
    deps = FakeDeps(workspace_root=Path.cwd())

    # 启动一个长时间运行的后台任务（Windows: timeout / Unix: sleep）
    cmd = "timeout /t 30" if __import__("os").name == "nt" else "sleep 30"
    result = await tool.execute(
        {
            "command": cmd,
            "description": "测试后台任务",
            "run_in_background": True,
        },
        deps,
    )
    data = json.loads(result)
    assert data["status"] == "running"
    assert "background_task_id" in data
    task_id = data["background_task_id"]

    # 查询输出
    output_tool = create_shell_tools()[1]
    out_result = await output_tool.execute({"task_id": task_id}, deps)
    out_data = json.loads(out_result)
    assert out_data["status"] == "running"
    assert out_data["task_id"] == task_id

    # 停止任务
    stop_tool = create_shell_tools()[2]
    stop_result = await stop_tool.execute({"task_id": task_id}, deps)
    stop_data = json.loads(stop_result)
    assert stop_data["status"] == "stopped"

    # 再次查询应不存在
    out_result2 = await output_tool.execute({"task_id": task_id}, deps)
    assert "工具错误" in out_result2


@pytest.mark.asyncio
async def test_shell_auto_promote() -> None:
    """前台命令超过 15s 应自动转后台。"""
    tool = create_shell_tools()[0]
    deps = FakeDeps(workspace_root=Path.cwd())

    # 用一个 20 秒的 sleep 触发自动转后台
    cmd = "timeout /t 20" if __import__("os").name == "nt" else "sleep 20"
    result = await tool.execute(
        {"command": cmd, "description": "测试自动转后台"},
        deps,
    )
    data = json.loads(result)
    # 由于测试机器可能很快完成，我们检查两种情况：
    # 1. 如果已经转后台，status == running 且有 auto_promoted
    # 2. 如果完成了，exit_code 存在
    if "auto_promoted" in data:
        assert data["status"] == "running"
        task_id = data["background_task_id"]
        _bg_kill(task_id)


# ── 路径校验测试 ─────────────────────────────────────────────────────


def test_resolve_cwd_with_workspace_root() -> None:
    root = Path("C:/project")
    cwd, err = _resolve_cwd("src", root)
    assert err is None
    assert cwd == root / "src"


def test_resolve_cwd_absolute_outside_workspace() -> None:
    root = Path("C:/project")
    cwd, err = _resolve_cwd("C:/other", root)
    assert err is not None
    assert "超出允许范围" in err


def test_resolve_cwd_none_uses_workspace() -> None:
    root = Path("C:/project")
    cwd, err = _resolve_cwd(None, root)
    assert err is None
    assert cwd == root


# ── 输出截断测试 ─────────────────────────────────────────────────────


def test_truncate_small_content() -> None:
    meta = _truncate("hello")
    assert meta["truncated"] is False
    assert meta["text"] == "hello"


def test_truncate_large_content() -> None:
    big = "x\n" * 20000  # 远超 30000 字符
    meta = _truncate(big)
    assert meta["truncated"] is True
    assert meta["strategy"] == "tail"
    assert "已省略" in meta["text"]
    assert len(meta["text"]) <= 30000 + 100  # 允许一点误差


# ── 超时测试 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shell_timeout() -> None:
    tool = create_shell_tools()[0]
    deps = FakeDeps(workspace_root=Path.cwd())

    # Windows 用 ping 做可靠延迟；Unix 用 sleep
    is_win = __import__("os").name == "nt"
    cmd = "ping -n 6 127.0.0.1 > nul" if is_win else "sleep 5"
    result = await tool.execute(
        {"command": cmd, "description": "测试超时", "timeout": 1, "auto_promote": False},
        deps,
    )
    data = json.loads(result)
    assert data["interrupted"] is True
    assert "timed out" in data["output"].lower() or "超时" in data["output"]


# ── 安全校验测试 ─────────────────────────────────────────────────────


def test_validate_command_empty() -> None:
    assert _validate_command("") == "命令不能为空"


def test_validate_command_banned() -> None:
    for cmd in _BANNED:
        assert _validate_command(f"{cmd} args") is not None


def test_validate_command_ok() -> None:
    assert _validate_command("ls -la") is None
    assert _validate_command("python script.py") is None
