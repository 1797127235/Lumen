"""文档解析器 — 将 RawBytes 解析为 StructuredDocument。"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from backend.core.logging import get_logger
from backend.modules.data_sources.ingestion.connector import DocumentSection, RawBytes, StructuredDocument

logger = get_logger(__name__)


class DocumentParser(ABC):
    """文档解析器抽象基类。"""

    @abstractmethod
    def supports(self, mime_type: str | None, extension: str) -> bool:
        """判断是否支持该 mime-type 或扩展名。"""

    @abstractmethod
    def parse(self, raw: RawBytes) -> StructuredDocument:
        """将 RawBytes 解析为结构化文档。"""


# ── Markdown 解析器 ──


class MarkdownParser(DocumentParser):
    """解析 Markdown 文件，提取 frontmatter、标题层级、链接。"""

    def supports(self, mime_type: str | None, extension: str) -> bool:
        if mime_type:
            return mime_type in {"text/markdown", "text/x-markdown"}
        return extension.lower() in {".md", ".markdown", ".mdown"}

    def parse(self, raw: RawBytes) -> StructuredDocument:
        text = raw.content_bytes.decode("utf-8", errors="ignore")
        lines = text.splitlines()

        frontmatter: dict[str, Any] = {}
        content_lines = lines[:]
        if len(lines) >= 3 and lines[0].strip() == "---":
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    try:
                        import yaml

                        frontmatter = yaml.safe_load("\n".join(lines[1:i])) or {}
                        content_lines = lines[i + 1 :]
                    except (ImportError, Exception) as exc:
                        logger.debug("frontmatter.parse_failed", error=str(exc))
                    break

        content = "\n".join(content_lines).strip()
        title = frontmatter.get("title", "")
        if not title:
            for line in content_lines:
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            if not title:
                title = Path(raw.uri).stem

        sections = self._parse_sections(content_lines)
        wiki_links, external_links = self._extract_links(content)

        return StructuredDocument(
            data_source_id=raw.data_source_id,
            external_id=raw.external_id,
            uri=raw.uri,
            title=title,
            content=content,
            sections=sections,
            metadata={**raw.metadata, **frontmatter},
            wiki_links=wiki_links,
            external_links=external_links,
            content_hash=raw.content_hash,
            user_id=raw.user_id,
        )

    def _parse_sections(self, lines: list[str]) -> list[DocumentSection]:
        """解析 Markdown 标题层级结构。"""
        sections: list[DocumentSection] = []
        current_start = 0
        current_level = 0
        current_title = ""

        for i, line in enumerate(lines):
            match = re.match(r"^(#{1,6})\s+(.+)", line)
            if match:
                if current_level > 0:
                    sections.append(
                        DocumentSection(
                            level=current_level,
                            title=current_title,
                            content="\n".join(lines[current_start:i]).strip(),
                            start_line=current_start + 1,
                            end_line=i,
                        )
                    )
                current_level = len(match.group(1))
                current_title = match.group(2).strip()
                current_start = i + 1

        if current_level > 0:
            sections.append(
                DocumentSection(
                    level=current_level,
                    title=current_title,
                    content="\n".join(lines[current_start:]).strip(),
                    start_line=current_start + 1,
                    end_line=len(lines),
                )
            )

        return sections

    def _extract_links(self, content: str) -> tuple[list[str], list[str]]:
        """提取 Markdown 内部链接 [[...]] 和外部链接 [...](...)。

        Returns:
            (wiki_links, external_links)
        """
        # WikiLinks: [[Target]] 或 [[Target|Display]]
        wiki_links = list(set(re.findall(r"\[\[([^|\]]+)(?:\|[^\]]+)?\]\]", content)))
        # 外部链接: [text](url)
        external_links = list(set(url for _, url in re.findall(r"\[([^\]]+)\]\((https?://[^\)]+)\)", content)))
        return wiki_links, external_links


# ── PDF 解析器 ──


class PDFParser(DocumentParser):
    """解析 PDF 文件，提取文本和表格内容。"""

    def supports(self, mime_type: str | None, extension: str) -> bool:
        if mime_type:
            return mime_type == "application/pdf"
        return extension.lower() == ".pdf"

    def parse(self, raw: RawBytes) -> StructuredDocument:
        text = self._extract_text(raw.content_bytes)
        lines = text.splitlines()

        title = ""
        for line in lines[:5]:
            stripped = line.strip()
            if stripped and len(stripped) < 100:
                title = stripped
                break
        if not title:
            title = Path(raw.uri).stem

        return StructuredDocument(
            data_source_id=raw.data_source_id,
            external_id=raw.external_id,
            uri=raw.uri,
            title=title,
            content=text,
            sections=[],
            metadata={**raw.metadata, "parser": "pdf"},
            wiki_links=[],
            external_links=[],
            content_hash=raw.content_hash,
            user_id=raw.user_id,
        )

    def _extract_text(self, data: bytes) -> str:
        """提取 PDF 文本。优先 pypdf，回退到可打印字符扫描。"""
        try:
            from io import BytesIO

            from pypdf import PdfReader

            reader = PdfReader(BytesIO(data))
            pages: list[str] = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    pages.append(page_text)
            return "\n\n".join(pages)
        except ImportError:
            logger.debug("pdf.pypdf_not_installed")
        except Exception as exc:
            logger.warning("pdf.pypdf_failed", error=str(exc))

        return self._extract_printable_text(data)

    def _extract_printable_text(self, data: bytes) -> str:
        """最终回退：提取可打印 ASCII 字符块。"""
        chunks: list[str] = []
        current: list[str] = []
        for b in data:
            if 32 <= b < 127 or b in (9, 10, 13):
                current.append(chr(b))
            else:
                if len(current) >= 4:
                    chunks.append("".join(current))
                current = []
        if len(current) >= 4:
            chunks.append("".join(current))
        return "\n".join(chunks)


# ── HTML 解析器 ──


class HTMLParser(DocumentParser):
    """解析 HTML 文件，提取正文内容。"""

    def supports(self, mime_type: str | None, extension: str) -> bool:
        if mime_type:
            return mime_type in {"text/html", "application/xhtml+xml"}
        return extension.lower() in {".html", ".htm", ".xhtml"}

    def parse(self, raw: RawBytes) -> StructuredDocument:
        text = self._extract_text(raw.content_bytes)
        lines = text.splitlines()

        title = ""
        for line in lines[:3]:
            stripped = line.strip()
            if stripped and len(stripped) < 100:
                title = stripped
                break
        if not title:
            title = Path(raw.uri).stem

        return StructuredDocument(
            data_source_id=raw.data_source_id,
            external_id=raw.external_id,
            uri=raw.uri,
            title=title,
            content=text,
            sections=[],
            metadata={**raw.metadata, "parser": "html"},
            wiki_links=[],
            external_links=[],
            content_hash=raw.content_hash,
            user_id=raw.user_id,
        )

    def _extract_text(self, data: bytes) -> str:
        """提取 HTML 正文。优先 BeautifulSoup，回退到正则。"""
        html = data.decode("utf-8", errors="ignore")

        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")

            for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()

            for selector in ["article", "main", "[role='main']", ".content", "#content"]:
                elem = soup.select_one(selector)
                if elem:
                    text = elem.get_text(separator="\n", strip=True)
                    if len(text) > 200:
                        return text

            body = soup.find("body")
            if body:
                return body.get_text(separator="\n", strip=True)

            return soup.get_text(separator="\n", strip=True)
        except ImportError:
            logger.debug("html.beautifulsoup_not_installed")
        except Exception as exc:
            logger.warning("html.bs4_failed", error=str(exc))

        return self._extract_text_regex(html)

    def _extract_text_regex(self, html: str) -> str:
        """无 BeautifulSoup 时的简单 HTML 文本提取。"""
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "\n", html)
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())


# ── 纯文本解析器 ──


class PlaintextParser(DocumentParser):
    """解析纯文本文件（.txt）。"""

    def supports(self, mime_type: str | None, extension: str) -> bool:
        if mime_type:
            return mime_type == "text/plain"
        return extension.lower() == ".txt"

    def parse(self, raw: RawBytes) -> StructuredDocument:
        text = raw.content_bytes.decode("utf-8", errors="ignore").strip()
        lines = text.splitlines()

        title = ""
        for line in lines[:3]:
            stripped = line.strip()
            if stripped and len(stripped) < 80:
                title = stripped
                break
        if not title:
            title = Path(raw.uri).stem

        return StructuredDocument(
            data_source_id=raw.data_source_id,
            external_id=raw.external_id,
            uri=raw.uri,
            title=title,
            content=text,
            sections=[],
            metadata=raw.metadata,
            wiki_links=[],
            external_links=[],
            content_hash=raw.content_hash,
            user_id=raw.user_id,
        )


# ── 注册表 ──

_PARSER_REGISTRY: list[DocumentParser] = [
    MarkdownParser(),
    PDFParser(),
    HTMLParser(),
    PlaintextParser(),
]


def get_parser(mime_type: str | None, extension: str) -> DocumentParser | None:
    """根据 mime-type 或扩展名获取合适的解析器。"""
    for parser in _PARSER_REGISTRY:
        if parser.supports(mime_type, extension):
            return parser
    return None


def parse_raw_bytes(raw: RawBytes) -> StructuredDocument:
    """使用注册表中的解析器解析 RawBytes，无匹配时回退到纯文本。"""
    extension = Path(raw.uri).suffix
    parser = get_parser(raw.mime_type, extension)

    if parser:
        try:
            return parser.parse(raw)
        except Exception as exc:
            logger.warning(
                "parser.failed",
                external_id=raw.external_id,
                mime_type=raw.mime_type,
                error=str(exc),
            )

    # Fallback：当作纯文本处理
    text = raw.content_bytes.decode("utf-8", errors="ignore").strip()
    return StructuredDocument(
        data_source_id=raw.data_source_id,
        external_id=raw.external_id,
        uri=raw.uri,
        title=Path(raw.uri).stem,
        content=text,
        sections=[],
        metadata=raw.metadata,
        wiki_links=[],
        external_links=[],
        content_hash=raw.content_hash,
        user_id=raw.user_id,
    )
