"""HTML 文件提取器。"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def html_to_markdown(html: str) -> str:
    """简单 HTML 转 markdown。"""
    try:
        from markdownify import markdownify

        return markdownify(html, heading_style="ATX", code_language="fenced")
    except ImportError:
        import re

        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\n\s*\n", "\n\n", text)
        return text.strip()


def extract_html(file_path: str) -> str:
    """从 html 文件提取 markdown 文本。"""
    path = Path(file_path)
    if not path.exists():
        raise ValueError(f"文件不存在: {file_path}")

    html = path.read_text(encoding="utf-8")
    if not html.strip():
        raise ValueError("HTML 文件内容为空")

    return html_to_markdown(html)
