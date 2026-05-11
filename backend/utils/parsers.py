"""文件解析器 — 格式路由器模式。

按文件扩展名分发到对应的解析策略：
- 纯文本 (md/txt/markdown/rst/csv/json)：直接读取
- 其他格式：markitdown 统一处理
- 图片：拒绝（需要 llm_client）

后续可针对特定格式替换解析器（如 PDF 换 pymupdf4llm），只需修改 _PARSER_MAP。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path, PurePosixPath
from typing import Protocol

from markitdown import MarkItDown


class ParseResult:
    __slots__ = ("text", "metadata")

    def __init__(self, text: str, metadata: dict | None = None):
        self.text = text
        self.metadata = metadata or {}


class FileParser(Protocol):
    async def __call__(self, filename: str, content: bytes) -> ParseResult: ...


# ── 解析策略 ──────────────────────────────────


async def _parse_plain_text(filename: str, content: bytes) -> ParseResult:
    """纯文本直接读取，不走 markitdown。"""
    ext = PurePosixPath(filename).suffix.lower().lstrip(".")
    text = content.decode("utf-8", errors="replace")
    return ParseResult(text, {"chars": len(text), "ext": ext, "parser": "plain"})


_converter = MarkItDown()


async def _parse_markitdown(filename: str, content: bytes) -> ParseResult:
    """markitdown 统一解析（PDF/DOCX/PPTX/XLSX/HTML/EPUB 等）。"""
    ext = PurePosixPath(filename).suffix.lower().lstrip(".")

    if ext in _IMAGE_EXTENSIONS:
        raise ValueError(f"图片文件 .{ext} 暂不支持直接索引。" "请将内容整理为文本文件后上传。")

    suffix = PurePosixPath(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = await asyncio.to_thread(_converter.convert, tmp_path)
        text = result.text_content or ""
    except Exception as e:
        raise ValueError(f"无法解析文件 .{ext}: {e}") from e
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if not text.strip():
        raise ValueError(f"文件 .{ext} 内容为空或无法提取文本")

    return ParseResult(text, {"chars": len(text), "ext": ext, "parser": "markitdown"})


# ── 未来增强：PDF 专用解析器（示例） ──────────
# async def _parse_pdf_pymupdf(filename: str, content: bytes) -> ParseResult:
#     """pymupdf4llm — 社区推荐的最佳 PDF→MD 方案。"""
#     import pymupdf4llm
#     import fitz
#     doc = fitz.open(stream=content, filetype="pdf")
#     text = pymupdf4llm.to_markdown(doc)
#     return ParseResult(text, {"chars": len(text), "ext": "pdf", "parser": "pymupdf4llm"})


# ── 格式路由表 ────────────────────────────────

_PLAIN_EXTS = {"md", "txt", "markdown", "rst", "csv", "json"}

_IMAGE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "bmp",
    "tiff",
    "webp",
    "svg",
}

_PARSER_MAP: dict[str, FileParser] = {
    **{ext: _parse_plain_text for ext in _PLAIN_EXTS},
}

_DEFAULT_PARSER: FileParser = _parse_markitdown

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".xlsx",
    ".xls",
    ".html",
    ".htm",
    ".csv",
    ".json",
    ".md",
    ".txt",
    ".markdown",
    ".rst",
    ".epub",
    ".ipynb",
    ".zip",
}


async def parse_file(filename: str, content: bytes) -> ParseResult:
    """解析上传文件，返回 ParseResult(text, metadata)。"""
    ext = PurePosixPath(filename).suffix.lower().lstrip(".")

    if ext in _IMAGE_EXTENSIONS:
        raise ValueError(f"图片文件 .{ext} 暂不支持直接索引。" "请将内容整理为文本文件后上传。")

    parser = _PARSER_MAP.get(ext, _DEFAULT_PARSER)
    return await parser(filename, content)
