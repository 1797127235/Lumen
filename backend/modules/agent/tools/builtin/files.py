"""文件工具 Handlers — 只做业务逻辑，不处理路径策略。

路径解析、越界检查、循环检测由 Dispatcher + PathPolicy + LoopGuardPolicy 统一处理。
Handler 接收的 args 中已包含 '_resolved_path'（Path 对象）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.core.logging import get_logger
from backend.modules.agent.tools.builtin.schemas import (
    FileListArgs,
    FileReadArgs,
    FileSearchArgs,
    FileWriteArgs,
)
from backend.modules.agent.tools.core.context import ToolRuntimeContext
from backend.modules.agent.tools.file_security import (
    DEFAULT_MAX_READ_CHARS,
    check_size_limits,
    is_binary_file,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from typing import reveal_type  # noqa: F401


# ── file_read ──


async def handle_file_read(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """读取文本文件内容，返回带行号的文本。"""
    typed: FileReadArgs = args  # type: ignore[assignment]
    resolved: Path = typed["_resolved_path"]
    offset = max(1, typed.get("offset", 1))
    limit = max(1, min(typed.get("limit", 500), 2000))

    # 文件存在性检查（虽然 dispatcher 已做，但 handler 再确认一次）
    if not resolved.exists():
        return f"文件不存在: {resolved}"

    if resolved.is_dir():
        return f"{resolved.name} 是目录，请使用 file_list 列出内容"

    # 大小限制（先读取内容，再检查字符数）
    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except UnicodeDecodeError:
        return f"无法读取 {resolved.name}：文件编码不支持（可能是二进制文件）"
    except OSError as exc:
        return f"读取失败: {exc}"

    file_size = resolved.stat().st_size
    size_msg = check_size_limits(file_size, len(content))
    if size_msg:
        return size_msg

    # 二进制检测
    is_bin, is_img = is_binary_file(resolved, content)
    if is_bin:
        if is_img:
            return f"{resolved.name} 是图片文件，无法以文本形式读取。"
        return f"{resolved.name} 是二进制文件，无法以文本形式读取。"

    # 截断
    if len(content) > DEFAULT_MAX_READ_CHARS:
        content = content[:DEFAULT_MAX_READ_CHARS] + "\n... [内容已截断]"

    # 分页
    lines = content.splitlines()
    total_lines = len(lines)
    start_idx = offset - 1
    end_idx = min(start_idx + limit, total_lines)
    selected = lines[start_idx:end_idx]

    # 添加行号
    numbered = [f"{i + 1:6d}|{line}" for i, line in enumerate(selected, start=start_idx)]
    result = "\n".join(numbered)

    # 分页提示
    if end_idx < total_lines:
        result += f"\n... ({total_lines - end_idx} 行未显示，使用 offset={end_idx + 1} 继续)"

    return result


# ── file_write ──


async def handle_file_write(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """写入或覆盖文本文件。"""
    typed: FileWriteArgs = args  # type: ignore[assignment]
    resolved: Path = typed["_resolved_path"]
    content = typed.get("content", "")

    # 确保父目录存在
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"创建目录失败: {exc}"

    file_existed = resolved.exists()

    try:
        resolved.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"写入失败: {exc}"

    action = "更新" if file_existed else "创建"
    return f"{action}成功: {resolved.name} ({len(content)} 字符)"


# ── file_list ──


async def handle_file_list(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """列出目录内容。"""
    typed: FileListArgs = args  # type: ignore[assignment]
    # path 可选，默认为 cwd
    resolved: Path | None = typed.get("_resolved_path")

    if resolved is None:
        # 没有 path 参数，使用 cwd
        resolved = ctx.cwd or ctx.workspace_root
        if resolved is None:
            return "错误：未配置工作区"

    if not resolved.exists():
        return f"目录不存在: {resolved}"

    if resolved.is_file():
        return f"{resolved.name} 是文件，请使用 file_read 读取内容"

    try:
        entries = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return f"无法读取目录: {exc}"

    lines: list[str] = []
    dirs: list[str] = []
    files: list[str] = []

    for entry in entries:
        name = entry.name
        try:
            if entry.is_dir():
                dirs.append(f"  📁 {name}/")
            else:
                size = entry.stat().st_size
                size_str = _format_size(size)
                files.append(f"  📄 {name} ({size_str})")
        except OSError:
            files.append(f"  📄 {name} (?)")

    lines.extend(dirs)
    lines.extend(files)

    header = f"目录: {resolved}\n{'=' * 40}"
    return header + "\n" + "\n".join(lines) if lines else header + "\n(空目录)"


# ── file_search ──


async def handle_file_search(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """在指定目录下递归搜索文件。"""
    typed: FileSearchArgs = args  # type: ignore[assignment]
    pattern = typed["pattern"]
    resolved: Path | None = typed.get("_resolved_path")

    if resolved is None:
        resolved = ctx.cwd or ctx.workspace_root
        if resolved is None:
            return "错误：未配置工作区"

    if not pattern:
        return "必须提供 pattern 参数"

    if not resolved.exists():
        return f"目录不存在: {resolved}"

    if resolved.is_file():
        return f"{resolved.name} 是文件，不是目录"

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return f"正则表达式错误: {exc}"

    matches: list[str] = []
    max_results = 50

    try:
        for item in resolved.rglob("*"):
            if item.is_file() and regex.search(item.name):
                rel = item.relative_to(resolved)
                matches.append(str(rel))
                if len(matches) >= max_results:
                    matches.append(f"... (结果过多，只显示前 {max_results} 个)")
                    break
    except OSError as exc:
        return f"搜索失败: {exc}"

    if not matches:
        return f"在 '{resolved}' 下未找到匹配 '{pattern}' 的文件"

    header = f"搜索 '{pattern}' 于 '{resolved}'：\n{'=' * 40}"
    return header + "\n" + "\n".join(matches)


# ── 辅助 ──


def _format_size(size: int) -> str:
    """格式化文件大小。"""
    s = float(size)
    for unit in ["B", "KB", "MB"]:
        if s < 1024:
            return f"{s:.1f}{unit}" if unit != "B" else f"{int(s)}{unit}"
        s /= 1024
    return f"{s:.1f}GB"
