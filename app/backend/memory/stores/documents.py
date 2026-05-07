"""文件系统存储 — 原始档案的真相源。

按 user_id → doc_type 组织目录。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.backend.config import USER_DATA_DIR
from app.backend.logging_config import get_logger

logger = get_logger(__name__)


class DocumentStore:
    """文件系统存储。

    使用方法：
        store = DocumentStore()
        rel_path = store.save(user_id, "resume", "my_resume.pdf", file_bytes)
        content = store.read(rel_path)
        store.delete(rel_path)
    """

    def __init__(self) -> None:
        self._base_dir = USER_DATA_DIR / "files"

    def _ensure_dir(self, user_id: str, doc_type: str) -> Path:
        path = self._base_dir / user_id / doc_type
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save(self, user_id: str, doc_type: str, filename: str, content: bytes) -> str:
        """保存文件到 user_id/doc_type/ 下，返回相对路径（相对 files/）。

        文件名加时间戳前缀避免重名。
        """
        dir_path = self._ensure_dir(user_id, doc_type)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = f"{ts}_{filename}"
        file_path = dir_path / safe_name
        file_path.write_bytes(content)
        rel = str(file_path.relative_to(self._base_dir))
        logger.debug("Document saved", rel_path=rel, size=len(content))
        return rel

    def read(self, rel_path: str) -> bytes:
        """根据相对路径读取文件内容。"""
        full_path = self._base_dir / rel_path
        if not full_path.exists():
            raise FileNotFoundError(f"Document not found: {rel_path}")
        return full_path.read_bytes()

    def delete(self, rel_path: str) -> bool:
        """删除文件。返回文件是否存在。"""
        full_path = self._base_dir / rel_path
        if full_path.exists():
            full_path.unlink()
            return True
        return False

    def get_absolute_path(self, rel_path: str) -> Path:
        return self._base_dir / rel_path
