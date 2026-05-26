"""文件系统工具 — file_read / file_write / file_ls / file_grep"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import subprocess
import time
from difflib import unified_diff
from pathlib import Path
from typing import Any

from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok

MAX_READ_LINES = 2000
MAX_READ_BYTES = 50 * 1024
MAX_LINE_LENGTH = 2000

_BINARY_EXTS = {
    ".pyc",
    ".pyo",
    ".exe",
    ".dll",
    ".so",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".bin",
    ".dat",
    ".wasm",
    ".class",
    ".jar",
    ".war",
    ".obj",
    ".o",
    ".a",
    ".lib",
}

_SESSION_FILES_DIR = Path.home() / ".lumen" / "session-files"

_DOCLING_EXTS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".odt",
    ".ods",
    ".odp",
    ".tex",
    ".rtf",
}

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _resolve(raw_path: str, workspace_root: str, *, allow_session_files: bool = False) -> tuple[str, str | None]:
    # 仅 file_read 允许 session-files（只读附件副本）
    if allow_session_files and os.path.isabs(raw_path):
        resolved = os.path.realpath(raw_path)
        real_session = os.path.realpath(str(_SESSION_FILES_DIR))
        if resolved == real_session or resolved.startswith(real_session + os.sep):
            return resolved, None
        return resolved, f"路径不在允许的 session-files 范围内：{resolved}"

    # file_write / file_grep：保持原有 workspace_root 沙箱
    if not os.path.isabs(raw_path):
        raw_path = os.path.join(workspace_root, raw_path)
    resolved = os.path.realpath(raw_path)
    real_root = os.path.realpath(workspace_root)
    if resolved != real_root and not resolved.startswith(real_root + os.sep):
        return resolved, f"路径超出工作区范围：{resolved}"
    return resolved, None


def _is_binary(path: str) -> bool:
    if os.path.splitext(path)[1].lower() in _BINARY_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            sample = f.read(4096)
        if not sample:
            return False
        if b"\x00" in sample:
            return True
        non_print = sum(1 for b in sample if b < 9 or (13 < b < 32))
        return non_print / len(sample) > 0.3
    except OSError:
        return False


# ── Docling 进程隔离 ──


def _docling_worker(file_path: str, result_queue: mp.Queue) -> None:
    """在独立进程中运行 Docling，父进程可通过 terminate() 强制 kill。"""
    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(file_path)
        text = result.document.export_to_markdown()
        result_queue.put(("ok", text))
    except Exception as e:
        result_queue.put(("error", str(e)))


async def _read_docling(file_path: str, max_length: int):
    """Docling 在子进程中运行，超时 10s 后 terminate → kill 强制终止。"""
    result_queue: mp.Queue = mp.Queue()
    process = mp.Process(target=_docling_worker, args=(file_path, result_queue))
    process.start()

    try:
        start = time.time()
        while time.time() - start < 10.0:
            if not result_queue.empty():
                status, payload = result_queue.get()
                await asyncio.to_thread(process.join)
                if status == "error":
                    return tool_error(f"文档解析失败：{payload}")
                text = payload
                if len(text) > max_length:
                    text = text[:max_length] + f"\n\n[已截断，原文件共 {len(text)} 字符]"
                return tool_ok(text)
            await asyncio.sleep(0.05)
        raise TimeoutError()
    except TimeoutError:
        process.terminate()
        await asyncio.to_thread(process.join, timeout=2.0)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join)
        return await _read_pdf_fallback(file_path, max_length)


async def _read_pdf_fallback(file_path: str, max_length: int):
    try:
        import pypdf

        def _extract():
            reader = pypdf.PdfReader(file_path)
            texts = []
            for page in reader.pages[:20]:
                text = page.extract_text()
                if text:
                    texts.append(text)
            return "\n\n".join(texts)

        result = await asyncio.to_thread(_extract)
        if len(result) > max_length:
            result = result[:max_length] + f"\n\n[已截断，原文件共 {len(result)} 字符]"
        return tool_ok(result)
    except Exception as e:
        return tool_error(f"PDF 降级提取失败：{e}")


# ── Tool 实现 ──


async def _file_read(args: dict[str, Any], deps):
    raw = args.get("file_path", "").strip()
    if not raw:
        return tool_error("请提供 file_path")

    resolved, err = _resolve(raw, str(deps.workspace_root), allow_session_files=True)
    if err:
        return tool_error(err)
    if not os.path.exists(resolved):
        return tool_error(f"文件不存在：{resolved}")

    if os.path.isdir(resolved):
        try:
            entries = sorted(os.listdir(resolved))
            offset = max(1, int(args.get("offset", 1)))
            limit = min(int(args.get("limit", MAX_READ_LINES)), MAX_READ_LINES)
            start = offset - 1
            sliced = entries[start : start + limit]
            lines = sorted([e + "/" if os.path.isdir(os.path.join(resolved, e)) else e for e in sliced])
            truncated = start + len(sliced) < len(entries)
            out = f"<path>{resolved}</path>\n<type>directory</type>\n<entries>\n"
            out += "\n".join(lines)
            if truncated:
                out += f"\n\n（显示 {len(sliced)}/{len(entries)} 项，使用 offset={offset + len(sliced)} 继续）"
            else:
                out += f"\n\n（共 {len(entries)} 项）"
            return tool_ok(out + "\n</entries>")
        except PermissionError:
            return tool_error(f"无权访问目录：{resolved}")

    ext = os.path.splitext(resolved)[1].lower()

    # ── 文档文件：Docling 解析，用 max_length 截断 ──
    if ext in _DOCLING_EXTS:
        file_size = os.path.getsize(resolved)
        if file_size > 20 * 1024 * 1024:
            return tool_error(f"文件过大：{file_size / 1024 / 1024:.1f}MB（最大 20MB）")
        max_length = min(int(args.get("max_length", 10000)), 50000)
        return await _read_docling(resolved, max_length)

    # ── 图片：提示模型通过 vision 查看 ──
    if ext in _IMAGE_EXTS:
        return tool_ok("[图片文件，已作为视觉输入直接提供]")

    # ── 文本文件：保持现有 offset/limit 行分页逻辑 ──
    if _is_binary(resolved):
        return tool_error(f"不支持读取二进制文件：{resolved}")

    offset = max(1, int(args.get("offset", 1)))
    limit = min(int(args.get("limit", MAX_READ_LINES)), MAX_READ_LINES)

    try:
        raw_lines: list[str] = []
        total = 0
        bytes_read = 0
        truncated = False

        with open(resolved, encoding="utf-8", errors="replace") as f:
            for line in f:
                total += 1
                if total < offset:
                    continue
                if len(raw_lines) >= limit:
                    truncated = True
                    break
                line = line.rstrip("\n")
                if len(line) > MAX_LINE_LENGTH:
                    line = line[:MAX_LINE_LENGTH] + f"...（已截断至 {MAX_LINE_LENGTH} 字符）"
                size = len(line.encode("utf-8"))
                if bytes_read + size > MAX_READ_BYTES:
                    truncated = True
                    break
                raw_lines.append(line)
                bytes_read += size

        if not raw_lines and offset > total:
            return tool_error(f"offset {offset} 超出文件行数（共 {total} 行）")

        out = f"<path>{resolved}</path>\n<type>file</type>\n<content>\n"
        out += "\n".join(f"{i + offset}: {line}" for i, line in enumerate(raw_lines))
        last = offset + len(raw_lines) - 1
        if truncated:
            out += f"\n\n（已截断，显示第 {offset}–{last} 行，使用 offset={last + 1} 继续）"
        else:
            out += f"\n\n（文件共 {total} 行）"
        return tool_ok(out + "\n</content>")

    except OSError as e:
        return tool_error(f"读取失败：{e}")


async def _file_write(args: dict[str, Any], deps):
    raw = args.get("file_path", "").strip()
    content = args.get("content", "")
    if not raw:
        return tool_error("请提供 file_path")

    resolved, err = _resolve(raw, str(deps.workspace_root))
    if err:
        return tool_error(err)

    MAX_DIFF_SIZE = 1024 * 1024  # 1MB

    old_lines: list[str] = []
    skip_diff = False
    if os.path.exists(resolved):
        try:
            size = os.path.getsize(resolved)
            if size > MAX_DIFF_SIZE:
                skip_diff = True
            else:
                with open(resolved, encoding="utf-8", errors="replace") as f:
                    old_lines = f.readlines()
        except OSError:
            pass

    new_lines = [ln + "\n" for ln in content.splitlines()] if content else []

    try:
        parent = os.path.dirname(resolved)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return tool_error(f"写入失败：{e}")

    rel = os.path.relpath(resolved, str(deps.workspace_root))
    if skip_diff:
        return tool_ok(f"已写入 {rel}（文件过大，diff 省略）")

    diff_lines = list(unified_diff(old_lines, new_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm=""))
    if diff_lines:
        diff = "\n".join(diff_lines[:100])
        return tool_ok(f"已写入 {rel}\n\n```diff\n{diff}\n```")
    return tool_ok(f"已写入 {rel}（内容未变化）")


async def _file_ls(args: dict[str, Any], deps):
    raw = args.get("path", "").strip() or str(deps.workspace_root)
    resolved, err = _resolve(raw, str(deps.workspace_root))
    if err:
        return tool_error(err)
    if not os.path.exists(resolved):
        return tool_error(f"路径不存在：{resolved}")
    if not os.path.isdir(resolved):
        return tool_error(f"不是目录：{resolved}")

    try:
        entries = sorted(os.listdir(resolved))
        lines = [e + "/" if os.path.isdir(os.path.join(resolved, e)) else e for e in entries]
        rel = os.path.relpath(resolved, str(deps.workspace_root)) + "/"
        return tool_ok(rel + "\n" + "\n".join(lines))
    except PermissionError:
        return tool_error(f"无权访问：{resolved}")


async def _file_grep(args: dict[str, Any], deps):
    pattern = args.get("pattern", "").strip()
    search_path = args.get("path", "").strip() or str(deps.workspace_root)
    include = args.get("include", "").strip()

    if not pattern:
        return tool_error("请提供搜索 pattern")

    resolved, err = _resolve(search_path, str(deps.workspace_root))
    if err:
        return tool_error(err)

    cmd = ["rg", "--line-number", "--no-heading", "--color=never", pattern, resolved]
    if include:
        cmd += ["--glob", include]

    try:
        result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=15)
        output = result.stdout.strip()
        if not output:
            return tool_ok("未找到匹配内容。")
        lines = output.splitlines()
        if len(lines) > 200:
            return tool_ok("\n".join(lines[:200]) + "\n\n（结果已截断，仅显示前 200 行）")
        return tool_ok("\n".join(lines))
    except FileNotFoundError:
        return tool_error("未找到 ripgrep（rg），请先安装：https://github.com/BurntSushi/ripgrep")
    except subprocess.TimeoutExpired:
        return tool_error("搜索超时（15 秒）")


def create_file_tools() -> list[ToolDef]:
    return [
        ToolDef(
            name="file_read",
            description="读取文件内容或列出目录。支持分页（offset/limit）。对 PDF/DOCX 等文档使用 max_length 截断。",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件或目录路径（绝对路径或相对于工作区）"},
                    "offset": {"type": "integer", "description": "起始行号（1 开始，默认 1），仅对文本文件生效"},
                    "limit": {"type": "integer", "description": "最多读取行数（默认 2000），仅对文本文件生效"},
                    "max_length": {
                        "type": "integer",
                        "description": "最大返回字符数，仅对 PDF/DOCX/PPTX 等文档类型生效",
                    },
                },
                "required": ["file_path"],
            },
            execute=_file_read,
            read_only=True,
            meta=ToolMeta(always_on=True, risk="read-only", search_hint="读取文件、查看文件内容"),
        ),
        ToolDef(
            name="file_write",
            description="写入文件内容（创建或完整覆盖）。返回 diff。路径必须在工作区内。",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "目标文件路径"},
                    "content": {"type": "string", "description": "写入的完整内容"},
                },
                "required": ["file_path", "content"],
            },
            execute=_file_write,
            read_only=False,
            meta=ToolMeta(always_on=False, risk="write", search_hint="写文件、覆盖文件、创建文件"),
        ),
        ToolDef(
            name="file_ls",
            description="列出目录内容。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径（默认工作区根目录）"},
                },
            },
            execute=_file_ls,
            read_only=True,
            meta=ToolMeta(always_on=True, risk="read-only", search_hint="列出目录、查看文件夹"),
        ),
        ToolDef(
            name="file_grep",
            description="在工作区内用正则搜索文件内容（需要 ripgrep）。",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "path": {"type": "string", "description": "搜索目录（默认工作区根目录）"},
                    "include": {"type": "string", "description": "文件 glob 过滤，如 *.py"},
                },
                "required": ["pattern"],
            },
            execute=_file_grep,
            read_only=True,
            meta=ToolMeta(always_on=True, risk="read-only", search_hint="搜索文件内容、grep、正则匹配"),
        ),
    ]
