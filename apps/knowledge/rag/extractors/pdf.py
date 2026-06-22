"""PDF 文件提取器。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_CHARS_PER_PAGE = 50


@dataclass
class PdfExtractResult:
    """PDF 提取结果。"""

    text: str
    pages: int


class PdfParseError(Exception):
    """PDF 解析错误。"""


async def extract_pdf(file_path: str) -> PdfExtractResult:
    """从 PDF 提取文本。"""
    try:
        import pdfplumber
    except ImportError:
        raise PdfParseError("需要安装 pdfplumber: pip install pdfplumber")

    path = Path(file_path)
    if not path.exists():
        raise PdfParseError(f"文件不存在: {file_path}")

    try:
        texts: list[str] = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                texts.append(text)
            pages = len(pdf.pages)

        full_text = "\n\n".join(texts).strip()

        if len(full_text) < MIN_CHARS_PER_PAGE * max(1, pages):
            raise PdfParseError("该 PDF 几乎没有可提取的文字（可能是扫描件或图片型 PDF）。")

        return PdfExtractResult(text=full_text, pages=pages)

    except PdfParseError:
        raise
    except Exception as e:
        raise PdfParseError(f"PDF 解析失败: {e}") from e
