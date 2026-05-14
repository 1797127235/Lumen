"""NullProvider — 什么都不做。用于测试或用户不想装任何向量库时降级。"""

from __future__ import annotations

from typing import Any

from backend.modules.data_sources.ingestion.document_index_provider import DocumentIndexProvider


class NullProvider(DocumentIndexProvider):
    """什么都不做。用于测试或用户不想装任何向量库时降级。"""

    @classmethod
    def provider_name(cls) -> str:
        return "null"

    @classmethod
    def is_available(cls) -> bool:
        return True

    def initialize(self) -> None:
        pass

    async def prefetch(self, query: str) -> str:
        return ""

    async def sync_document(
        self,
        content: str,
        doc_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

    def get_tool_schemas(self) -> list[dict]:
        return []
