"""摄入状态 store — 线程安全 JSON 原子写入。"""

from __future__ import annotations

import contextlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


class IngestionStore:
    """追踪外部文档的索引状态。

    状态结构：
      indexed: {doc_id: {hash, indexed_at, source_id}}
      failed:  {doc_id: {reason, retry_count, last_attempt}}
      last_scan: {source_id: ISO timestamp}
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"indexed": {}, "failed": {}, "last_scan": {}}
        if path.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                self._state = json.loads(path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        """原子写入：tempfile → replace，防止写入中途崩溃。"""
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            suffix=".tmp",
            delete=False,
        ) as tmp:
            json.dump(self._state, tmp, ensure_ascii=False, indent=2)
            tmp_path = Path(tmp.name)
        tmp_path.replace(self.path)

    def is_indexed(self, doc_id: str, content_hash: str) -> bool:
        with self._lock:
            entry = self._state["indexed"].get(doc_id)
            return entry is not None and entry.get("hash") == content_hash

    def mark_indexed(self, doc_id: str, content_hash: str, source_id: str) -> None:
        with self._lock:
            self._state["indexed"][doc_id] = {
                "hash": content_hash,
                "indexed_at": datetime.now(UTC).isoformat(),
                "source_id": source_id,
            }
            self._state["failed"].pop(doc_id, None)
            self._save()

    def mark_failed(self, doc_id: str, reason: str) -> None:
        with self._lock:
            entry = self._state["failed"].get(doc_id, {"retry_count": 0})
            entry["reason"] = reason
            entry["retry_count"] = entry.get("retry_count", 0) + 1
            entry["last_attempt"] = datetime.now(UTC).isoformat()
            self._state["failed"][doc_id] = entry
            self._save()

    def get_retry_count(self, doc_id: str) -> int:
        with self._lock:
            return self._state["failed"].get(doc_id, {}).get("retry_count", 0)

    def set_last_scan(self, source_id: str) -> None:
        with self._lock:
            self._state["last_scan"][source_id] = datetime.now(UTC).isoformat()
            self._save()
