"""文件局部编辑工具 — SEARCH/REPLACE 式编辑。"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Generator
from difflib import unified_diff
from typing import Any

from lib.tools._base import ToolDef, ToolMeta, tool_error, tool_ok
from shared.logging import get_logger

logger = get_logger(__name__)

_BOM_UTF8 = b"\xef\xbb\xbf"


# ── 文件 IO ──


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


def _escape_norm(content: str, find: str) -> Generator[str, None, None]:
    def unesc(s: str) -> str:
        return s.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")

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


# ── 工具 ──


async def _file_edit(args: dict[str, Any], deps):
    raw_path = args.get("file_path", "").strip()
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))

    if not raw_path:
        return tool_error("请提供 file_path")
    if old_string == new_string:
        return tool_error("old_string 和 new_string 相同，无需修改")

    from lib.tools.files import _resolve

    resolved, err = _resolve(raw_path, str(deps.workspace_root))
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
