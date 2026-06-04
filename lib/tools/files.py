"""文件系统工具 — file_read / file_write / file_ls / file_grep / file_edit"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import re
import subprocess
import time
from collections.abc import Generator
from difflib import unified_diff
from typing import Any

from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok
from lib.tools._path_safety import is_read_denied, is_write_denied
from shared.logging import get_logger

logger = get_logger(__name__)

_BOM_UTF8 = b"\xef\xbb\xbf"

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


def _resolve_read(raw_path: str, workspace_root: str) -> tuple[str, str | None]:
    """解析读路径：相对路径以 workspace_root 为基准，realpath 后检查读黑名单。"""
    if not os.path.isabs(raw_path):
        raw_path = os.path.join(workspace_root, raw_path)
    resolved = os.path.realpath(raw_path)
    if os.path.islink(raw_path):
        return resolved, f"拒绝符号链接：{raw_path}"
    err = is_read_denied(resolved)
    if err:
        return resolved, err
    return resolved, None


def _resolve_write(raw_path: str, workspace_root: str) -> tuple[str, str | None]:
    """解析写路径：相对路径以 workspace_root 为基准，realpath 后检查写黑名单。"""
    if not os.path.isabs(raw_path):
        raw_path = os.path.join(workspace_root, raw_path)
    resolved = os.path.realpath(raw_path)
    if os.path.islink(raw_path):
        return resolved, f"拒绝符号链接：{raw_path}"
    err = is_write_denied(resolved)
    if err:
        return resolved, err
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

    resolved, err = _resolve_read(raw, str(deps.workspace_root))
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

    resolved, err = _resolve_write(raw, str(deps.workspace_root))
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
    resolved, err = _resolve_read(raw, str(deps.workspace_root))
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

    resolved, err = _resolve_read(search_path, str(deps.workspace_root))
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


# ═══════════════════════════════════════════════════════════════════
#  file_edit — SEARCH/REPLACE 局部编辑
# ═══════════════════════════════════════════════════════════════════

# ── 文件 IO（编辑专用）──


def _read_file(path: str) -> tuple[str, bool]:
    """读取文件，返回 (内容, 是否有 BOM)。"""
    with open(path, "rb") as f:
        raw = f.read()
    has_bom = raw.startswith(_BOM_UTF8)
    text = raw[len(_BOM_UTF8) :].decode("utf-8") if has_bom else raw.decode("utf-8")
    return text, has_bom


def _write_file(path: str, content: str, has_bom: bool) -> None:
    """写入文件，根据原 BOM 状态决定是否添加。先 split 去重防止双 BOM。"""
    if content.startswith("\ufeff"):
        content = content[1:]
    if has_bom:
        content = "\ufeff" + content
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


# ── 换行符 ──


def _detect_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n")


def _convert(text: str, ending: str) -> str:
    return text if ending == "\n" else text.replace("\n", "\r\n")


# ── 并发锁 ──

_LOCKS: dict[str, asyncio.Lock] = {}


def _get_lock(path: str) -> asyncio.Lock:
    resolved = os.path.realpath(path)
    if resolved not in _LOCKS:
        _LOCKS[resolved] = asyncio.Lock()
    return _LOCKS[resolved]


# ── Replacer ──


def _simple(content: str, find: str) -> Generator[str, None, None]:
    if find in content:
        yield find


def _line_trimmed(content: str, find: str) -> Generator[str, None, None]:
    orig_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()

    for i in range(len(orig_lines) - len(search_lines) + 1):
        if all(orig_lines[i + j].strip() == search_lines[j].strip() for j in range(len(search_lines))):
            start = sum(len(orig_lines[k]) + 1 for k in range(i))
            end = start + sum(
                len(orig_lines[i + k]) + (1 if k < len(search_lines) - 1 else 0) for k in range(len(search_lines))
            )
            yield content[start:end]


def _block_anchor(content: str, find: str) -> Generator[str, None, None]:
    orig = content.split("\n")
    search = find.split("\n")
    if len(search) < 3:
        return
    if search[-1] == "":
        search.pop()

    first = search[0].strip()
    last = search[-1].strip()
    size = len(search)

    candidates = []
    for i in range(len(orig)):
        if orig[i].strip() != first:
            continue
        for j in range(i + 2, len(orig)):
            if orig[j].strip() == last:
                candidates.append((i, j))
                break

    if not candidates:
        return

    def _block(start: int, end: int) -> str:
        s = sum(len(orig[k]) + 1 for k in range(start))
        e = s
        for k in range(start, end + 1):
            e += len(orig[k])
            if k < end:
                e += 1
        return content[s:e]

    def _sim(start: int, end: int) -> float:
        actual = end - start + 1
        check = min(size - 2, actual - 2)
        if check <= 0:
            return 1.0
        sim = 0.0
        for j in range(1, size - 1):
            if j >= actual - 1:
                break
            a = orig[start + j].strip()
            b = search[j].strip()
            m = max(len(a), len(b))
            if m == 0:
                continue
            sim += (1 - _lev(a, b) / m) / check
        return sim

    if len(candidates) == 1:
        s, e = candidates[0]
        if _sim(s, e) >= 0.0:
            yield _block(s, e)
        return

    best = None
    max_sim = -1.0
    for s, e in candidates:
        sim = _sim(s, e)
        if sim > max_sim:
            max_sim = sim
            best = (s, e)

    if max_sim >= 0.3 and best:
        yield _block(best[0], best[1])


def _whitespace_norm(content: str, find: str) -> Generator[str, None, None]:
    def norm(t: str) -> str:
        return re.sub(r"\s+", " ", t).strip()

    nf = norm(find)
    lines = content.split("\n")

    for line in lines:
        if norm(line) == nf:
            yield line
        elif nf in norm(line):
            words = find.strip().split()
            if words:
                try:
                    pat = "\\s+".join(re.escape(w) for w in words)
                    m = re.search(pat, line)
                    if m:
                        yield m.group(0)
                except re.error:
                    pass

    fl = find.split("\n")
    if len(fl) > 1:
        for i in range(len(lines) - len(fl) + 1):
            block = "\n".join(lines[i : i + len(fl)])
            if norm(block) == nf:
                yield block


def _indent_flex(content: str, find: str) -> Generator[str, None, None]:
    def deindent(t: str) -> str:
        lines = t.split("\n")
        non_empty = [ln for ln in lines if ln.strip()]
        if not non_empty:
            return t
        indents: list[int] = []
        for ln in non_empty:
            m = re.match(r"^(\s*)", ln)
            indents.append(len(m.group(1)) if m else 0)
        min_indent = min(indents)
        return "\n".join(ln if ln.strip() == "" else ln[min_indent:] for ln in lines)

    nf = deindent(find)
    cl = content.split("\n")
    fl = find.split("\n")
    for i in range(len(cl) - len(fl) + 1):
        block = "\n".join(cl[i : i + len(fl)])
        if deindent(block) == nf:
            yield block


_ESCAPE_MAP = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "'": "'",
    '"': '"',
    "`": "`",
    "\\": "\\",
    "\n": "\n",
    "$": "$",
}
_ESCAPE_RE = re.compile(r"\\(n|t|r|'|\"|`|\\|\n|\$)")


def _escape_norm(content: str, find: str) -> Generator[str, None, None]:
    def unesc(s: str) -> str:
        return _ESCAPE_RE.sub(lambda m: _ESCAPE_MAP[m.group(1)], s)

    uf = unesc(find)
    if uf in content:
        yield uf

    cl = content.split("\n")
    fl = uf.split("\n")
    for i in range(len(cl) - len(fl) + 1):
        block = "\n".join(cl[i : i + len(fl)])
        if unesc(block) == uf:
            yield block


def _trim_boundary(content: str, find: str) -> Generator[str, None, None]:
    tf = find.strip()
    if tf == find:
        return
    if tf in content:
        yield tf
    cl = content.split("\n")
    fl = find.split("\n")
    for i in range(len(cl) - len(fl) + 1):
        block = "\n".join(cl[i : i + len(fl)])
        if block.strip() == tf:
            yield block


def _context_aware(content: str, find: str) -> Generator[str, None, None]:
    fl = find.split("\n")
    if len(fl) < 3:
        return
    if fl[-1] == "":
        fl.pop()

    cl = content.split("\n")
    first = fl[0].strip()
    last = fl[-1].strip()

    for i in range(len(cl)):
        if cl[i].strip() != first:
            continue
        for j in range(i + 2, len(cl)):
            if cl[j].strip() != last:
                continue
            block = cl[i : j + 1]
            if len(block) != len(fl):
                break
            match = 0
            total = 0
            for k in range(1, len(block) - 1):
                a = block[k].strip()
                b = fl[k].strip()
                if a or b:
                    total += 1
                    if a == b:
                        match += 1
            if total == 0 or match / total >= 0.5:
                yield "\n".join(block)
            break


def _multi_occurrence(content: str, find: str) -> Generator[str, None, None]:
    start = 0
    while True:
        idx = content.find(find, start)
        if idx == -1:
            break
        yield find
        start = idx + len(find)


# ── Levenshtein ──


def _lev(a: str, b: str) -> int:
    if not a or not b:
        return max(len(a), len(b))
    m = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        m[i][0] = i
    for j in range(len(b) + 1):
        m[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            m[i][j] = min(m[i - 1][j] + 1, m[i][j - 1] + 1, m[i - 1][j - 1] + cost)
    return m[len(a)][len(b)]


# ── replace ──

_REPLACERS = [
    _simple,
    _line_trimmed,
    _block_anchor,
    _whitespace_norm,
    _indent_flex,
    _escape_norm,
    _trim_boundary,
    _context_aware,
    _multi_occurrence,
]


def replace(content: str, old: str, new: str, replace_all: bool = False) -> str:
    if old == new:
        raise ValueError("No changes to apply: old_string and new_string are identical.")

    for replacer in _REPLACERS:
        for search in replacer(content, old):
            try:
                idx = content.index(search)
            except ValueError:
                continue

            if replace_all:
                return content.replace(search, new)

            if content.rindex(search) != idx:
                continue
            return content[:idx] + new + content[idx + len(search) :]

    any_match = False
    for replacer in _REPLACERS:
        for search in replacer(content, old):
            try:
                content.index(search)
                any_match = True
                break
            except ValueError:
                continue
        if any_match:
            break

    if any_match:
        raise ValueError(
            "Found multiple matches for old_string. "
            "Provide more surrounding context to make the match unique, "
            "or use replace_all=True to change every instance."
        )
    raise ValueError(
        "Could not find old_string in the file. "
        "It must match exactly, including whitespace, indentation, and line endings."
    )


# ── diff ──


def trim_diff(diff: str) -> str:
    lines = diff.split("\n")
    content_lines = [
        ln
        for ln in lines
        if (ln.startswith("+") or ln.startswith("-") or ln.startswith(" "))
        and not ln.startswith("---")
        and not ln.startswith("+++")
    ]
    if not content_lines:
        return diff

    min_indent = float("inf")
    for line in content_lines:
        c = line[1:]
        if c.strip():
            m = re.match(r"^(\s*)", c)
            if m:
                min_indent = min(min_indent, len(m.group(1)))

    if min_indent == float("inf") or min_indent == 0:
        return diff

    out = []
    for line in lines:
        if (
            (line.startswith("+") or line.startswith("-") or line.startswith(" "))
            and not line.startswith("---")
            and not line.startswith("+++")
        ):
            out.append(line[0] + line[1 + min_indent :])
        else:
            out.append(line)
    return "\n".join(out)


# ── 行号前缀清洗 ──

_LINE_PREFIX = re.compile(r"^\d+:\s")


def _strip_line_prefixes(text: str) -> str:
    lines = text.split("\n")
    sample = [ln for ln in lines[:5] if ln.strip()]
    if sample and all(_LINE_PREFIX.match(ln) for ln in sample):
        return "\n".join(_LINE_PREFIX.sub("", ln) for ln in lines)
    return text


# ── file_edit 工具 ──


async def _file_edit(args: dict[str, Any], deps):
    raw_path = args.get("file_path", "").strip()
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))

    if not raw_path:
        return tool_error("请提供 file_path")
    if old_string == new_string:
        return tool_error("old_string 和 new_string 相同，无需修改")

    resolved, err = _resolve_write(raw_path, str(deps.workspace_root))
    if err:
        return tool_error(err)

    async with _get_lock(resolved):
        if old_string == "":
            if os.path.exists(resolved):
                return tool_error(f"文件已存在：{resolved}。old_string 为空时只能创建新文件")
            parent = os.path.dirname(resolved)
            if parent:
                os.makedirs(parent, exist_ok=True)
            _write_file(resolved, new_string, False)
            rel = os.path.relpath(resolved, str(deps.workspace_root))
            return tool_ok(f"已创建 {rel}")

        if not os.path.exists(resolved):
            return tool_error(f"文件不存在：{resolved}")

        content, has_bom = _read_file(resolved)
        ending = _detect_ending(content)

        old_norm = _normalize(old_string)
        new_norm = _normalize(new_string)
        old_norm = _strip_line_prefixes(old_norm)

        if ending == "\r\n":
            old_norm = _convert(old_norm, "\r\n")
            new_norm = _convert(new_norm, "\r\n")

        try:
            new_content = replace(content, old_norm, new_norm, replace_all)
        except ValueError as e:
            return tool_error(str(e))

        _write_file(resolved, new_content, has_bom)

        rel = os.path.relpath(resolved, str(deps.workspace_root))
        old_lines = content.splitlines()
        new_lines = new_content.splitlines()
        diff = "\n".join(unified_diff(old_lines, new_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm=""))
        diff = trim_diff(diff)

        dl = diff.split("\n")
        additions = sum(1 for ln in dl if ln.startswith("+") and not ln.startswith("+++"))
        deletions = sum(1 for ln in dl if ln.startswith("-") and not ln.startswith("---"))

    return tool_ok(
        f"已编辑 {rel}\n\n```diff\n{diff}\n```",
        diff=diff,
        additions=additions,
        deletions=deletions,
    )


def create_edit_tool() -> ToolDef:
    return ToolDef(
        name="file_edit",
        description=(
            "在文件中局部替换文本。必须先用 file_read 读取文件后再编辑。\n\n"
            "使用规则：\n"
            "- old_string 必须精确匹配文件中的内容（包括空格、缩进、换行符）\n"
            "- 如果 old_string 在文件中出现多次，提供更长的上下文使其唯一，或设 replace_all=true\n"
            "- old_string 为空字符串时，只能创建新文件（文件已存在会报错）\n"
            "- 编辑失败时，根据错误提示调整 old_string 后再试\n"
            "- 支持从 file_read 的输出直接复制（自动去除行号前缀）"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要编辑的文件路径"},
                "old_string": {
                    "type": "string",
                    "description": "要替换的文本（可从 file_read 直接复制）",
                },
                "new_string": {"type": "string", "description": "替换后的新文本"},
                "replace_all": {
                    "type": "boolean",
                    "description": "是否替换所有出现（默认只替换第一个唯一匹配）",
                    "default": False,
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
        execute=_file_edit,
        read_only=False,
        meta=ToolMeta(
            always_on=True,
            risk="write",
            search_hint="编辑文件、修改文件、替换文本、局部修改",
        ),
    )


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
        create_edit_tool(),
    ]
