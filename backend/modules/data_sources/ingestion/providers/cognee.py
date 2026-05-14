"""CogneeProvider — DocumentIndexProvider 的 Cognee 实现。"""

from __future__ import annotations

from typing import Any

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
        """召回并拼装成字符串。

        使用 SemanticStore.search() 做召回，返回文本列表。
        """
        from backend.modules.memory.semantic_store import SemanticStore

        results = await SemanticStore().search(query, top_k=10)
        if not results:
            return ""
        return "\n\n".join([f"[来源: {i+1}]\n{r[:500]}" for i, r in enumerate(results)])

    async def sync_document(
        self,
        content: str,
        doc_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Cognee 内部自动分块，无需外部干预。

        使用 SemanticStore.ingest() 摄入文本。
        """
        from backend.modules.memory.datasets import DATASET_KNOWLEDGE
        from backend.modules.memory.semantic_store import SemanticStore

        await SemanticStore().ingest(
            content,
            doc_id,
            dataset=metadata.get("dataset", DATASET_KNOWLEDGE) if metadata else DATASET_KNOWLEDGE,
        )

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
