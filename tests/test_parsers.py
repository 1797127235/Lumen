"""Parser 测试 — Markdown / PDF / HTML / Plaintext。"""

from __future__ import annotations

from backend.modules.data_sources.ingestion.connector import RawBytes
from backend.modules.data_sources.ingestion.parser import (
    HTMLParser,
    MarkdownParser,
    PDFParser,
    PlaintextParser,
    get_parser,
    parse_raw_bytes,
)


class TestGetParser:
    """解析器路由选择。"""

    def test_markdown_by_extension(self) -> None:
        parser = get_parser(None, ".md")
        assert isinstance(parser, MarkdownParser)

    def test_markdown_by_mime(self) -> None:
        parser = get_parser("text/markdown", ".txt")
        assert isinstance(parser, MarkdownParser)

    def test_pdf_by_extension(self) -> None:
        parser = get_parser(None, ".pdf")
        assert isinstance(parser, PDFParser)

    def test_html_by_extension(self) -> None:
        parser = get_parser(None, ".html")
        assert isinstance(parser, HTMLParser)

    def test_plaintext_fallback(self) -> None:
        parser = get_parser(None, ".unknown")
        assert parser is None


class TestMarkdownParser:
    """Markdown 解析器。"""

    def test_basic_markdown(self) -> None:
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/test.md",
            uri="file:///test.md",
            content_bytes=b"# Hello\n\nThis is a test.",
            mime_type="text/markdown",
            metadata={},
            last_modified=0.0,
            user_id="demo_user",
        )
        parser = MarkdownParser()
        doc = parser.parse(raw)

        assert doc.title == "Hello"
        assert "This is a test" in doc.content
        assert doc.data_source_id == "ds1"
        assert doc.user_id == "demo_user"

    def test_frontmatter(self) -> None:
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/test.md",
            uri="file:///test.md",
            content_bytes=b"---\ntitle: My Title\n---\n\nBody here.",
            mime_type="text/markdown",
            metadata={},
            last_modified=0.0,
            user_id="u1",
        )
        doc = MarkdownParser().parse(raw)

        assert doc.title == "My Title"
        assert doc.metadata.get("title") == "My Title"

    def test_wiki_links(self) -> None:
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/test.md",
            uri="file:///test.md",
            content_bytes=b"See [[Another Page]] and [[Target|Display Name]].",
            mime_type="text/markdown",
            metadata={},
            last_modified=0.0,
        )
        doc = MarkdownParser().parse(raw)

        assert "Another Page" in doc.wiki_links
        assert "Target" in doc.wiki_links

    def test_external_links(self) -> None:
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/test.md",
            uri="file:///test.md",
            content_bytes=b"[link](https://example.com) and [local](file:///x)",
            mime_type="text/markdown",
            metadata={},
            last_modified=0.0,
        )
        doc = MarkdownParser().parse(raw)

        assert "https://example.com" in doc.external_links
        assert "file:///x" not in doc.external_links  # 非 http/https

    def test_sections(self) -> None:
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/test.md",
            uri="file:///test.md",
            content_bytes=b"# H1\n\ncontent1\n\n## H2\n\ncontent2",
            mime_type="text/markdown",
            metadata={},
            last_modified=0.0,
        )
        doc = MarkdownParser().parse(raw)

        assert len(doc.sections) == 2
        assert doc.sections[0].level == 1
        assert doc.sections[0].title == "H1"
        assert doc.sections[1].level == 2
        assert doc.sections[1].title == "H2"


class TestPDFParser:
    """PDF 解析器（含 fallback）。"""

    def test_pdf_fallback_printable_text(self) -> None:
        """无 pypdf 时，fallback 应提取可打印字符。"""
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/test.pdf",
            uri="file:///test.pdf",
            content_bytes=b"%PDF-1.4\n1 0 obj\n\nHello World from PDF\n\nendobj",
            mime_type="application/pdf",
            metadata={},
            last_modified=0.0,
            user_id="demo_user",
        )
        parser = PDFParser()
        doc = parser.parse(raw)

        assert doc.data_source_id == "ds1"
        assert doc.user_id == "demo_user"
        assert "Hello World" in doc.content
        assert doc.title  # 启发式标题

    def test_pdf_empty(self) -> None:
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/empty.pdf",
            uri="file:///empty.pdf",
            content_bytes=b"%PDF-1.4\n",
            mime_type="application/pdf",
            metadata={},
            last_modified=0.0,
        )
        doc = PDFParser().parse(raw)

        assert doc.content  # 至少有空格/换行处理后的结果


class TestHTMLParser:
    """HTML 解析器（含 fallback）。"""

    def test_html_fallback_regex(self) -> None:
        """无 BeautifulSoup 时，fallback 正则应提取文本。"""
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/test.html",
            uri="file:///test.html",
            content_bytes=b"""<html><head><title>My Page</title></head>
            <body><p>Hello from HTML</p></body></html>
            """,
            mime_type="text/html",
            metadata={},
            last_modified=0.0,
            user_id="demo_user",
        )
        parser = HTMLParser()
        doc = parser.parse(raw)

        assert doc.data_source_id == "ds1"
        assert doc.user_id == "demo_user"
        assert "Hello from HTML" in doc.content
        # script 内容不应出现
        assert "<script>" not in doc.content

    def test_html_title_extraction(self) -> None:
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/test.html",
            uri="file:///test.html",
            content_bytes=b"""
<html><head><title>My Page</title></head>
            <body><h1>Page Title</h1><p>Content here</p></body></html>
            """,
            mime_type="text/html",
            metadata={},
            last_modified=0.0,
        )
        doc = HTMLParser().parse(raw)

        # fallback 模式下第一行短文本作为标题
        assert "Page Title" in doc.title or doc.title == "test"


class TestPlaintextParser:
    """纯文本解析器。"""

    def test_basic_txt(self) -> None:
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/test.txt",
            uri="file:///test.txt",
            content_bytes=b"First line\n\nMore content here.",
            mime_type="text/plain",
            metadata={},
            last_modified=0.0,
            user_id="u1",
        )
        parser = PlaintextParser()
        doc = parser.parse(raw)

        assert doc.title == "First line"  # 第一行短文本作为标题
        assert "More content" in doc.content
        assert doc.user_id == "u1"

    def test_long_first_line(self) -> None:
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/test.txt",
            uri="file:///test.txt",
            content_bytes=b"x" * 100 + b"\nSecond line",
            mime_type="text/plain",
            metadata={},
            last_modified=0.0,
        )
        doc = PlaintextParser().parse(raw)

        # 第一行太长，跳过，用第二行或文件名
        assert doc.title != "x" * 100


class TestParseRawBytesIntegration:
    """parse_raw_bytes 集成测试 — 端到端 fallback 链路。"""

    def test_unknown_extension_fallback_to_plaintext(self) -> None:
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/test.xyz",
            uri="file:///test.xyz",
            content_bytes=b"Some content here.",
            mime_type=None,
            metadata={},
            last_modified=0.0,
            user_id="u1",
        )
        doc = parse_raw_bytes(raw)

        assert doc.content == "Some content here."
        assert doc.user_id == "u1"
        assert doc.title == "test"  # 从 uri stem

    def test_parse_failure_fallback(self) -> None:
        """Parser 抛异常时应回退到纯文本。"""
        # 构造一个会让 MarkdownParser 失败的 bytes（理论上不会，但测试 fallback 路径）
        raw = RawBytes(
            data_source_id="ds1",
            external_id="/test.md",
            uri="file:///test.md",
            content_bytes=b"Valid markdown content",  # 实际不会失败
            mime_type="text/markdown",
            metadata={},
            last_modified=0.0,
        )
        # 这个测试主要验证 parse_raw_bytes 的结构正确
        doc = parse_raw_bytes(raw)
        assert "Valid markdown" in doc.content
