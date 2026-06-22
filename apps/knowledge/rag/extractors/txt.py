"""TXT/MD 文件提取器。"""

from pathlib import Path


def extract_txt(file_path: str) -> str:
    """从 txt/md 文件提取文本。"""
    content = Path(file_path).read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError("文件内容为空")
    return content
