"""文档提取器：支持 PDF/TXT/MD/DOCX/HTML。"""

from pathlib import Path

from .docx import extract_docx
from .html import extract_html
from .pdf import PdfExtractResult, extract_pdf
from .txt import extract_txt

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".markdown", ".docx", ".html", ".htm"}


def is_supported_format(file_path: str) -> bool:
    """检查文件扩展名是否支持。"""
    ext = Path(file_path).suffix.lower()
    return ext in SUPPORTED_EXTENSIONS


async def extract_document(file_path: str) -> str | PdfExtractResult:
    """从文件提取文本。"""
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        return await extract_pdf(file_path)
    elif ext in (".txt", ".md", ".markdown"):
        return extract_txt(file_path)
    elif ext == ".docx":
        return await extract_docx(file_path)
    elif ext in (".html", ".htm"):
        return extract_html(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {ext}。支持: pdf, txt, md, docx, html")
