"""Cognee 语义存储封装 — 薄封装，统一错误处理 + dataset 管理。

从 services/cognee_service.py 迁移。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from app.backend.config import USER_DATA_DIR
from app.backend.logging_config import get_logger
from app.backend.memory.cognee_admin.datasets import DATASET_PROFILE

logger = get_logger(__name__)


class SemanticStore:
    """Cognee 语义存储的统一封装。

    所有 Cognee 调用经由此类收敛，失败时降级返回空/False。
    """

    @staticmethod
    def build_event_content(event: Any) -> str | None:
        """从 GrowthEvent 构建适合 Cognee 存储的文本。

        原则：只有长文本/非结构化内容才进 Cognee，结构化事件（技能、画像）不进。
        """
        from app.backend.models.growth_event import GrowthEvent as _GE

        if not isinstance(event, _GE) or not event.payload_json:
            return None

        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return payload if isinstance(payload, str) else None

        # 结构化事件不建 Cognee
        if event.event_type in ("profile_updated", "skill_added", "skill_level_changed"):
            if event.event_type == "profile_updated" and payload.get("memory_md"):
                return payload["memory_md"]
            return None

        # 非结构化事件：构建可搜索文本
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
        """存储文本到 Cognee，标记待 cognify（定时批量处理）。

        用 add() 纯写入，不触发 cognify（cognify_loop 统一批量处理）。
        成功返回 True。失败不影响主流程（SQLite 已写入）。
        """
        try:
            import cognee as _cognee

            # add() 仅写入，不触发 cognify
            await _cognee.add(content, dataset_name=dataset)

            # 标记 cognify（thread-safe 全局变量）
            from app.backend.memory.cognee_admin.cognify_loop import mark_needs_cognify

            mark_needs_cognify()

            logger.debug("Cognee add ok", doc_id=doc_id, dataset=dataset, content_length=len(content))
            return True
        except Exception as exc:
            logger.error("Cognee ingest failed", doc_id=doc_id, dataset=dataset, error=str(exc))
            return False

    async def search(self, query: str, datasets: list[str] | None = None, top_k: int = 10) -> list[str]:
        """语义搜索。返回文本片段的列表。

        支持多 dataset。失败时返回空列表，调用方应 fallback 到 FTS5。
        """
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
        """从 Cognee 返回的各种对象类型中提取文本。

        ResponseQAEntry 有 answer + context（后者往往更丰富）。
        ResponseGraphEntry 有 text。
        """
        for attr in ("text", "answer", "context", "content"):
            val = getattr(result, attr, None)
            if val:
                return str(val)
        return None

    async def clear_index(self) -> bool:
        """删除 Cognee 数据目录（kuzu + lancedb），然后重新初始化。

        单用户模式：删除整个目录。
        """
        try:
            for name in ("kuzu", "lancedb"):
                path = Path(USER_DATA_DIR) / name
                if path.exists():
                    shutil.rmtree(path, ignore_errors=False)

            from app.backend.memory.cognee_admin.cognify_loop import init_cognee

            init_cognee()
            logger.info("Cognee index cleared and reinitialized")
            return True
        except Exception as exc:
            logger.error("Cognee clear index failed", error=str(exc))
            return False
