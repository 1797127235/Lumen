"""Shell 命令执行工具 — 支持前台/后台模式与安全护栏。

设计参考 akashic-agent 的 ShellTool，适配 Lumen 的 ToolDef 架构：
- 返回字符串（JSON 序列化）供 Agent 读取
- 使用 args.get("workspace_root") 作为默认工作目录沙箱
- Windows 环境适配（taskkill 进程树终止）
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok
from shared.logging import get_logger

logger = get_logger(__name__)

_IS_WINDOWS = os.name == "nt"
_DEFAULT_TIMEOUT = 60
_FG_THRESHOLD = 15
_MAX_TIMEOUT = 600
_BLOCKING_TIMEOUT = 21_600
_MAX_OUTPUT = 30_000
_STREAM_CHUNK_SIZE = 4096
_STREAM_DRAIN_GRACE_S = 0.2
_BG_TTL_S = 4 * 3600
_BG_EVICT_DELAY_S = 300

# 高风险命令黑名单
_BANNED = frozenset(
    {
        "nc",
        "netcat",
        "telnet",
        "lynx",
        "w3m",
        "links",
        "chrome",
        "chromium",
        "firefox",
        "safari",
        "msedge",
        "iexplore",
        "opera",
        "brave",
    }
)


# ── 后台任务注册表 ────────────────────────────────────────────────────


@dataclass
class _BackgroundTask:
    proc: Any
    log_path: str
    pump_task: asyncio.Task | None
    started_at: float
    wall_started_at_ms: int
    command: str = ""
    description: str = ""
    last_output_at_ms: int | None = None
    timeout_s: int | None = None
    timeout_handle: asyncio.TimerHandle | None = None
    finish_reason: str = "natural"


_BG_REGISTRY: dict[str, _BackgroundTask] = {}


async def _bg_pump(
    proc: Any,
    log_path: str,
    bg_task: _BackgroundTask,
    on_data: Callable[[str], None] | None = None,
) -> None:
    """持续从 stdout/stderr 读取并写入日志文件，直到进程退出。"""
    with open(log_path, "wb") as f:

        async def _drain_stream(stream) -> None:
            if stream is None:
                return
            while True:
                chunk = await stream.read(_STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
                f.flush()
                bg_task.last_output_at_ms = int(time.time() * 1000)
                if on_data is not None:
                    on_data(chunk.decode(errors="replace"))

        stdout_task = asyncio.create_task(_drain_stream(proc.stdout))
        stderr_task = asyncio.create_task(_drain_stream(proc.stderr))

        await proc.wait()

        try:
            await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task),
                timeout=_STREAM_DRAIN_GRACE_S,
            )
        except TimeoutError:
            stdout_task.cancel()
            stderr_task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)


def _schedule_eviction(task_id: str, log_path: str) -> None:
    """延迟清理后台任务注册表和日志文件。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    def _evict() -> None:
        task = _BG_REGISTRY.pop(task_id, None)
        if task is not None and task.timeout_handle is not None:
            task.timeout_handle.cancel()
        with contextlib.suppress(OSError):
            os.unlink(log_path)

    loop.call_later(_BG_EVICT_DELAY_S, _evict)


def _on_background_task_done(task_id: str, task: _BackgroundTask) -> None:
    _schedule_eviction(task_id, task.log_path)


def _subprocess_options(cwd: Path | None, env: dict[str, str] | None) -> dict[str, Any]:
    options: dict[str, Any] = {
        "cwd": str(cwd) if cwd is not None else None,
        "env": env,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if _IS_WINDOWS:
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    return options


def _kill_process_tree(proc: Any) -> None:
    if _IS_WINDOWS:
        result = subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def _bg_kill(task_id: str, *, finish_reason: str = "stopped") -> None:
    task = _BG_REGISTRY.pop(task_id, None)
    if task is None:
        return
    task.finish_reason = finish_reason
    if task.timeout_handle is not None:
        task.timeout_handle.cancel()
    with contextlib.suppress(ProcessLookupError, PermissionError):
        _kill_process_tree(task.proc)
    if task.pump_task is not None:
        task.pump_task.cancel()
    with contextlib.suppress(OSError):
        os.unlink(task.log_path)


def _bg_timeout(task_id: str) -> None:
    task = _BG_REGISTRY.get(task_id)
    if task is None:
        return
    _bg_kill(task_id, finish_reason="timeout")


def _arm_background_timeout(task_id: str, task: _BackgroundTask) -> None:
    if task.timeout_s is None:
        return
    remain_s = task.timeout_s - (time.monotonic() - task.started_at)
    if remain_s <= 0:
        _bg_timeout(task_id)
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task.timeout_handle = loop.call_later(remain_s, lambda: _bg_timeout(task_id))


def _validate_command(command: str) -> str | None:
    """基础安全校验：黑名单命令。"""
    if not command.strip():
        return "命令不能为空"
    base_cmd = command.split()[0].lower()
    if base_cmd in _BANNED:
        return f"命令 '{base_cmd}' 不被允许（安全限制）"
    return None


def _resolve_cwd(raw_cwd: str | None, workspace_root: Path | None) -> tuple[Path | None, str | None]:
    """解析并校验工作目录，确保不跳出 workspace_root。"""
    if raw_cwd is None or raw_cwd.strip() == "":
        return workspace_root, None

    if workspace_root is None:
        return Path(raw_cwd).resolve(), None

    raw_path = Path(raw_cwd)
    if not raw_path.is_absolute():
        resolved = (workspace_root / raw_path).resolve()
    else:
        resolved = raw_path.resolve()

    root_resolved = workspace_root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        return None, f"工作目录超出允许范围：{resolved}"
    return resolved, None


def _truncate(content: str) -> dict[str, Any]:
    """超过阈值时保留尾部，便于看到命令结果与错误摘要。"""
    if len(content) <= _MAX_OUTPUT:
        return {
            "text": content,
            "truncated": False,
            "strategy": "tail",
            "full_length": len(content),
            "returned_length": len(content),
            "omitted_lines": 0,
        }

    omitted = content[: len(content) - _MAX_OUTPUT]
    omitted_lines = omitted.count("\n")
    prefix = f"... [{omitted_lines} 行已省略] ...\n\n"
    tail_budget = max(0, _MAX_OUTPUT - len(prefix))
    tail = content[-tail_budget:] if tail_budget > 0 else ""
    text = prefix + tail
    return {
        "text": text,
        "truncated": True,
        "strategy": "tail",
        "full_length": len(content),
        "returned_length": len(text),
        "omitted_lines": omitted_lines,
    }


def _write_full_output(content: str) -> str:
    fd, path = tempfile.mkstemp(prefix="lumen-shell-", suffix=".log")
    os.close(fd)
    Path(path).write_text(content, encoding="utf-8")
    return path


def _shell_env() -> dict[str, str]:
    return os.environ.copy()


# ── Tool 实现 ────────────────────────────────────────────────────────


async def _shell(args: dict[str, Any], ctx: Any = None, **kwargs):
    command: str = args.get("command", "").strip()
    description: str = args.get("description", "")
    timeout_specified = "timeout" in args and args.get("timeout") is not None
    run_in_background: bool = bool(args.get("run_in_background", False))
    auto_promote: bool = bool(args.get("auto_promote", True))
    raw_cwd: str | None = args.get("cwd")

    max_timeout = _BLOCKING_TIMEOUT if not run_in_background and not auto_promote else _MAX_TIMEOUT
    default_timeout = (
        _BLOCKING_TIMEOUT if not run_in_background and not auto_promote and not timeout_specified else _DEFAULT_TIMEOUT
    )
    timeout: int = min(int(args.get("timeout", default_timeout)), max_timeout)

    if not command:
        return tool_error("命令不能为空")

    cmd_err = _validate_command(command)
    if cmd_err:
        return tool_error(cmd_err, "SAFETY")

    workspace_root = kwargs.get("workspace_root")
    if isinstance(workspace_root, str):
        workspace_root = Path(workspace_root)

    cwd, cwd_err = _resolve_cwd(raw_cwd, workspace_root)
    if cwd_err:
        return tool_error(cwd_err, "SAFETY")

    logger.info("shell [%s]: %s", description, command[:120])

    env = _shell_env()

    if run_in_background:
        bg_timeout = timeout if timeout_specified else None
        return await _execute_background(command, description, cwd, env, bg_timeout)

    return await _execute_foreground(command, description, cwd, env, timeout, timeout_specified, auto_promote)


async def _execute_background(
    command: str,
    description: str,
    cwd: Path | None,
    env: dict[str, str],
    timeout_s: int | None,
):
    task_id = f"shell_{uuid4().hex[:12]}"
    log_fd, log_path = tempfile.mkstemp(prefix=f"lumen-bg-{task_id}-", suffix=".log")
    os.close(log_fd)

    wall_start_ms = int(time.time() * 1000)
    proc = await asyncio.create_subprocess_shell(
        command,
        **_subprocess_options(cwd, env),
    )
    bg = _BackgroundTask(
        proc=proc,
        log_path=log_path,
        pump_task=None,
        started_at=time.monotonic(),
        wall_started_at_ms=wall_start_ms,
        command=command,
        description=description,
        timeout_s=timeout_s,
    )
    pump = asyncio.create_task(_bg_pump(proc, log_path, bg))
    pump.add_done_callback(lambda _: _on_background_task_done(task_id, bg))
    bg.pump_task = pump
    _BG_REGISTRY[task_id] = bg
    _arm_background_timeout(task_id, bg)
    logger.info("shell bg started [%s] pid=%s log=%s", task_id, proc.pid, log_path)

    return tool_ok(
        json.dumps(
            {
                "command": command,
                "background_task_id": task_id,
                "status": "running",
                "output_path": log_path,
                "started_at_ms": wall_start_ms,
                "timeout_s": timeout_s,
                "exit_code": None,
                "interrupted": False,
            },
            ensure_ascii=False,
        )
    )


async def _execute_foreground(
    command: str,
    description: str,
    cwd: Path | None,
    env: dict[str, str],
    timeout: int,
    timeout_specified: bool,
    auto_promote: bool,
):
    task_id = f"shell_{uuid4().hex[:12]}"
    log_fd, log_path = tempfile.mkstemp(prefix=f"lumen-fg-{task_id}-", suffix=".log")
    os.close(log_fd)

    wall_start_ms = int(time.time() * 1000)
    start_mono = time.monotonic()
    hard_timeout_s = timeout if timeout_specified else None

    proc = await asyncio.create_subprocess_shell(
        command,
        **_subprocess_options(cwd, env),
    )
    bg = _BackgroundTask(
        proc=proc,
        log_path=log_path,
        pump_task=None,
        started_at=start_mono,
        wall_started_at_ms=wall_start_ms,
        command=command,
        description=description,
        timeout_s=hard_timeout_s,
    )
    pump = asyncio.create_task(_bg_pump(proc, log_path, bg))
    bg.pump_task = pump

    fg_wait_timeout = min(timeout, _FG_THRESHOLD) if auto_promote else timeout
    try:
        await asyncio.wait_for(asyncio.shield(pump), timeout=fg_wait_timeout)
    except TimeoutError:
        elapsed_s = time.monotonic() - start_mono
        if not auto_promote or (timeout_specified and elapsed_s >= timeout):
            return await _finalize_timed_out(command, proc, pump, log_path, start_mono)

        # 自动转后台
        pump.add_done_callback(lambda _: _on_background_task_done(task_id, bg))
        _BG_REGISTRY[task_id] = bg
        _arm_background_timeout(task_id, bg)
        logger.info("shell auto-promoted [%s] pid=%s", task_id, proc.pid)
        return tool_ok(
            json.dumps(
                {
                    "command": command,
                    "background_task_id": task_id,
                    "status": "running",
                    "output_path": log_path,
                    "started_at_ms": wall_start_ms,
                    "timeout_s": hard_timeout_s,
                    "exit_code": None,
                    "interrupted": False,
                    "auto_promoted": True,
                },
                ensure_ascii=False,
            )
        )
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            _kill_process_tree(proc)
        pump.cancel()
        with contextlib.suppress(OSError):
            os.unlink(log_path)
        raise

    # 前台正常完成
    duration_ms = int((time.monotonic() - start_mono) * 1000)
    exit_code = proc.returncode or 0

    try:
        content = Path(log_path).read_bytes().decode(errors="replace")
    except OSError:
        content = ""
    finally:
        with contextlib.suppress(OSError):
            os.unlink(log_path)

    if not content:
        content = "（无输出）"
    elif exit_code != 0:
        content = content + f"\nExit code {exit_code}"

    output_meta = _truncate(content)
    full_output_path = _write_full_output(content) if output_meta["truncated"] else None
    truncation = None
    if output_meta["truncated"]:
        truncation = {
            "strategy": output_meta["strategy"],
            "full_length": output_meta["full_length"],
            "returned_length": output_meta["returned_length"],
            "omitted_lines": output_meta["omitted_lines"],
        }

    return tool_ok(
        json.dumps(
            {
                "command": command,
                "exit_code": exit_code,
                "interrupted": False,
                "duration_ms": duration_ms,
                "output": output_meta["text"],
                "truncation": truncation,
                "full_output_path": full_output_path,
            },
            ensure_ascii=False,
        )
    )


async def _finalize_timed_out(
    command: str,
    proc: Any,
    pump: asyncio.Task,
    log_path: str,
    start_mono: float,
):
    with contextlib.suppress(ProcessLookupError, PermissionError):
        _kill_process_tree(proc)

    try:
        await asyncio.wait_for(asyncio.shield(pump), timeout=_STREAM_DRAIN_GRACE_S)
    except TimeoutError:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

    duration_ms = int((time.monotonic() - start_mono) * 1000)
    try:
        content = Path(log_path).read_bytes().decode(errors="replace")
    except OSError:
        content = ""
    finally:
        with contextlib.suppress(OSError):
            os.unlink(log_path)

    if not content:
        content = "（无输出）"
    content = content + "\nCommand timed out"
    output_meta = _truncate(content)
    full_output_path = _write_full_output(content) if output_meta["truncated"] else None
    truncation = None
    if output_meta["truncated"]:
        truncation = {
            "strategy": output_meta["strategy"],
            "full_length": output_meta["full_length"],
            "returned_length": output_meta["returned_length"],
            "omitted_lines": output_meta["omitted_lines"],
        }

    return tool_ok(
        json.dumps(
            {
                "command": command,
                "exit_code": -1,
                "interrupted": True,
                "duration_ms": duration_ms,
                "output": output_meta["text"],
                "truncation": truncation,
                "full_output_path": full_output_path,
            },
            ensure_ascii=False,
        )
    )


async def _task_output(args: dict[str, Any], ctx: Any = None):
    task_id: str = args.get("task_id", "")
    block: bool = bool(args.get("block", False))
    timeout_ms: int = int(args.get("timeout_ms", 30000))

    task = _BG_REGISTRY.get(task_id)
    if task is None:
        return tool_error(f"任务 {task_id!r} 不存在或已清理")

    pump_task = task.pump_task
    if pump_task is None:
        return tool_error(f"任务 {task_id!r} 状态异常")

    if _is_background_timeout(task):
        _bg_timeout(task_id)
        return tool_error(f"任务 {task_id!r} 已超时，已自动终止")

    if block and not pump_task.done():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(pump_task), timeout=timeout_ms / 1000)

    done = pump_task.done()
    if done and time.monotonic() - task.started_at > _BG_TTL_S:
        _bg_registry_pop_and_cleanup(task_id, task)
        return tool_error(f"任务 {task_id!r} 已超出 TTL，已清理")

    exit_code = task.proc.returncode if done else None
    status = "done" if done else "running"

    now_ms = int(time.time() * 1000)
    elapsed_ms = now_ms - task.wall_started_at_ms
    since_last_output_ms = now_ms - task.last_output_at_ms if task.last_output_at_ms is not None else None

    try:
        content = Path(task.log_path).read_bytes().decode(errors="replace")
    except OSError:
        content = ""

    output_meta = _truncate(content)
    truncation = None
    if output_meta["truncated"]:
        truncation = {
            "strategy": output_meta["strategy"],
            "full_length": output_meta["full_length"],
            "returned_length": output_meta["returned_length"],
            "omitted_lines": output_meta["omitted_lines"],
        }

    return tool_ok(
        json.dumps(
            {
                "task_id": task_id,
                "status": status,
                "exit_code": exit_code,
                "elapsed_ms": elapsed_ms,
                "since_last_output_ms": since_last_output_ms,
                "output": output_meta["text"],
                "truncation": truncation,
                "output_path": task.log_path,
            },
            ensure_ascii=False,
        )
    )


async def _task_stop(args: dict[str, Any], ctx: Any = None):
    task_id: str = args.get("task_id", "")
    if task_id not in _BG_REGISTRY:
        return tool_ok(json.dumps({"task_id": task_id, "status": "not_found"}, ensure_ascii=False))
    _bg_kill(task_id)
    return tool_ok(json.dumps({"task_id": task_id, "status": "stopped"}, ensure_ascii=False))


# ── 辅助函数 ────────────────────────────────────────────────────────


def _is_background_timeout(task: _BackgroundTask) -> bool:
    if task.timeout_s is None:
        return False
    return time.monotonic() - task.started_at >= task.timeout_s


def _bg_registry_pop_and_cleanup(task_id: str, task: _BackgroundTask) -> None:
    _BG_REGISTRY.pop(task_id, None)
    if task.timeout_handle is not None:
        task.timeout_handle.cancel()
    with contextlib.suppress(OSError):
        os.unlink(task.log_path)


# ── ToolDef 工厂 ────────────────────────────────────────────────────


def create_shell_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="shell",
            description=(
                "在系统 shell 中执行命令并返回输出。\n\n"
                "适用场景：安装依赖、运行测试、启动服务、查看进程、git 操作、系统管理。\n\n"
                "⚠️ 不适用场景（有专用工具，不要用 shell 替代）：\n"
                "- 查询 RSS 内容 → 用 rss_list_items\n"
                "- 搜索文件内容 → 用 file_grep\n"
                "- 读取文件 → 用 file_read\n"
                "- 查询数据库 → 如果有专用工具就用专用工具\n\n"
                "后台任务处理（严格执行）：\n"
                '- 前台命令超过 15 秒会自动转为后台，返回 {"status":"running","background_task_id":"xxx"}\n'
                "- 收到 background_task_id 后，必须立即用 task_output(block=true,timeout_ms=30000) 等待结果\n"
                "- 严禁在后台任务 running 时重复执行相同命令\n"
                "- 如需放弃后台任务，先用 task_stop 终止，再执行新命令\n\n"
                "注意：\n"
                "- 默认工作目录为项目根目录，可用 cwd 指定相对路径\n"
                "- 输出超过 30000 字符时自动截断保留尾部\n"
                "- 前台命令默认超时 60 秒，超过 15 秒未完成会自动转为后台任务\n"
                "- 后台任务返回 background_task_id，用 task_output 查看、task_stop 终止"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    },
                    "description": {
                        "type": "string",
                        "description": "用 5-10 字描述命令作用，便于日志追踪",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 60，最大 600（阻塞模式最大 21600）",
                        "minimum": 1,
                        "maximum": _BLOCKING_TIMEOUT,
                    },
                    "run_in_background": {
                        "type": "boolean",
                        "description": "是否后台运行，默认 false",
                    },
                    "auto_promote": {
                        "type": "boolean",
                        "description": "前台超 15 秒是否自动转后台，默认 true",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "工作目录（相对项目根目录的相对路径）",
                    },
                },
                "required": ["command", "description"],
            },
            execute=_shell,
            read_only=False,
            meta=ToolMeta(always_on=False, risk="destructive", search_hint="执行命令、运行脚本、cmd、bash"),
        ),
        ToolDef(
            name="task_output",
            description=(
                "读取后台 shell 任务的当前输出和状态。\n"
                "返回字段：status / exit_code / elapsed_ms / since_last_output_ms / output\n"
                "block=true 可等待任务完成，设置合理 timeout_ms 避免轮询。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "shell 返回的 background_task_id",
                    },
                    "block": {
                        "type": "boolean",
                        "description": "是否等待完成后再返回，默认 false",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "block=true 时的最长等待毫秒数，默认 30000",
                        "minimum": 0,
                    },
                },
                "required": ["task_id"],
            },
            execute=_task_output,
            read_only=True,
            meta=ToolMeta(always_on=True, risk="read-only", search_hint="查看后台任务输出、task output"),
        ),
        ToolDef(
            name="task_stop",
            description="停止后台 shell 任务（SIGKILL 整棵进程树）并从注册表移除。",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "要停止的后台任务 ID",
                    },
                },
                "required": ["task_id"],
            },
            execute=_task_stop,
            read_only=False,
            meta=ToolMeta(always_on=False, risk="destructive", search_hint="停止任务、终止后台进程"),
        ),
    ]
