"""Cognee 语义存储封装 — 薄封装，统一错误处理 + dataset 管理。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from backend.config import USER_DATA_DIR
from backend.logging_config import get_logger
from backend.memory.classifier import is_indexable
from backend.memory.datasets import DATASET_PROFILE

logger = get_logger(__name__)


class SemanticStore:
    @staticmethod
    def build_event_content(event: Any) -> str | None:
        from backend.domain.models import GrowthEvent as _GE

        if not isinstance(event, _GE) or not event.payload_json:
            return None

        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return payload if isinstance(payload, str) else None

        # Profile 事件不进 Cognee — L0 已注入，索引会语义重复
        if not is_indexable(event.event_type):
            return None

        parts = [f"[{event.event_type}]"]
        if event.entity_type:
            parts.append(event.entity_type)
        if event.entity_id:
            parts.append(event.entity_id)
        for key in ("description", "content", "text", "feedback", "note"):
            if payload.get(key):
                parts.append(str(payload[key]))
        return " | ".join(parts) if parts else None

    async def ingest(self, content: str, doc_id: str, dataset: str = DATASET_PROFILE) -> bool:
        try:
            import cognee as _cognee

            await _cognee.add(content, dataset_name=dataset)

            from backend.memory.cognify_loop import mark_needs_cognify

            mark_needs_cognify()

            logger.debug("Cognee add ok", doc_id=doc_id, dataset=dataset, content_length=len(content))
            return True
        except Exception as exc:
            logger.error("Cognee ingest failed", doc_id=doc_id, dataset=dataset, error=str(exc))
            return False

    async def search(self, query: str, datasets: list[str] | None = None, top_k: int = 10) -> list[str]:
        try:
            import cognee as _cognee

            ds = datasets or [DATASET_PROFILE]
            raw = await _cognee.recall(query, datasets=ds, top_k=top_k)
            if not raw:
                return []
            return [t for t in (self._extract_text(r) for r in raw) if t]
        except Exception as exc:
            logger.warning("Cognee search failed, returning empty", query=query, error=str(exc))
            return []

    @staticmethod
    def _extract_text(result: Any) -> str | None:
        for attr in ("text", "answer", "context", "content"):
            val = getattr(result, attr, None)
            if val:
                return str(val)
        return None

    async def clear_index(self) -> bool:
        try:
            for name in ("kuzu", "lancedb"):
                path = Path(USER_DATA_DIR) / name
                if path.exists():
                    shutil.rmtree(path, ignore_errors=False)

            from backend.memory.cognify_loop import init_cognee

            init_cognee()
            logger.info("Cognee index cleared and reinitialized")
            return True
        except Exception as exc:
            logger.error("Cognee clear index failed", error=str(exc))
            return False
