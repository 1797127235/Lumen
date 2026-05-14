"""CogneeProvider — DocumentIndexProvider 的 Cognee 实现。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from backend.core.config import USER_DATA_DIR
from backend.core.logging import get_logger
from backend.modules.data_sources.ingestion.document_index_provider import DocumentIndexProvider

logger = get_logger(__name__)


class CogneeProvider(DocumentIndexProvider):
    """Cognee 实现。内部自动分块、向量化、建图。

    分块策略：Cognee 内部处理，外部不感知。
    """

    @classmethod
    def provider_name(cls) -> str:
        return "cognee"

    @classmethod
    def is_available(cls) -> bool:
        try:
            import cognee  # noqa: F401

            return True
        except ImportError:
            return False

    def initialize(self) -> None:
        pass

    async def prefetch(self, query: str) -> str:
        """召回并拼装成字符串。"""
        import cognee as _cognee

        from backend.modules.memory.datasets import DATASET_PROFILE

        try:
            raw = await _cognee.recall(query, datasets=[DATASET_PROFILE], top_k=10)
            if not raw:
                return ""
            results = [self._extract_text(r) for r in raw]
            results = [r for r in results if r]
            if not results:
                return ""
            return "\n\n".join([f"[来源: {i+1}]\n{r[:500]}" for i, r in enumerate(results)])
        except Exception as exc:
            logger.warning("Cognee search failed, returning empty", query=query, error=str(exc))
            return ""

    async def sync_document(
        self,
        content: str,
        doc_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Cognee 内部自动分块，无需外部干预。"""
        import cognee as _cognee

        from backend.modules.memory.cognify_loop import mark_needs_cognify
        from backend.modules.memory.datasets import DATASET_KNOWLEDGE

        dataset = metadata.get("dataset", DATASET_KNOWLEDGE) if metadata else DATASET_KNOWLEDGE
        try:
            await _cognee.add(content, dataset_name=dataset)
            mark_needs_cognify()
            logger.debug("Cognee add ok", doc_id=doc_id, dataset=dataset, content_length=len(content))
        except Exception as exc:
            logger.error("Cognee ingest failed", doc_id=doc_id, dataset=dataset, error=str(exc))

    async def clear(self) -> bool:
        """清空 Cognee 索引（删除底层存储目录并重新初始化）。"""
        try:
            for name in ("kuzu", "lancedb"):
                path = Path(USER_DATA_DIR) / name
                if path.exists():
                    shutil.rmtree(path, ignore_errors=False)

            from backend.modules.memory.cognify_loop import init_cognee

            init_cognee()
            logger.info("Cognee index cleared and reinitialized")
            return True
        except Exception as exc:
            logger.error("Cognee clear failed", error=str(exc))
            return False

    @staticmethod
    def _extract_text(result: Any) -> str | None:
        for attr in ("text", "answer", "context", "content"):
            val = getattr(result, attr, None)
            if val:
                return str(val)
        return None

    def get_tool_schemas(self) -> list[dict]:
        """暴露 data_source_search 工具给 Agent。"""
        return [
            {
                "name": "data_source_search",
                "description": "搜索外部数据源中的相关文档",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            }
        ]

    async def handle_tool_call(self, name: str, args: dict) -> str:
        if name == "data_source_search":
            return await self.prefetch(args["query"])
        raise NotImplementedError(f"Tool {name} not supported")
