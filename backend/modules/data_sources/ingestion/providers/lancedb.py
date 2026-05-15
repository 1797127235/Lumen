"""LanceDBProvider — DocumentIndexProvider 的 LanceDB 实现。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.core.config import USER_DATA_DIR
from backend.core.logging import get_logger
from backend.modules.data_sources.ingestion.document_index_provider import DocumentIndexProvider, ProviderHit

logger = get_logger(__name__)

DEFAULT_TABLE = "lumen_documents"


class LanceDBProvider(DocumentIndexProvider):
    """LanceDB 实现。使用向量索引做语义搜索。

    分块策略：简单 overlap 分块（chunk_size=512, overlap=50）。
    Embedding：使用 sentence-transformers 的 all-MiniLM-L6-v2。
    """

    @classmethod
    def provider_name(cls) -> str:
        return "lancedb"

    @classmethod
    def is_available(cls) -> bool:
        try:
            import lancedb  # noqa: F401
            import sentence_transformers  # noqa: F401

            return True
        except ImportError:
            return False

    def __init__(self, db_path: Path | None = None, table_name: str = DEFAULT_TABLE) -> None:
        self._db_path = db_path or (USER_DATA_DIR / "lancedb")
        self._table_name = table_name
        self._db: Any = None
        self._table: Any = None
        self._embedder: Any = None

    def initialize(self) -> None:
        """初始化 LanceDB 连接和 embedding 模型。"""
        import lancedb
        from sentence_transformers import SentenceTransformer

        self._db_path.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self._db_path))
        # Fix 8: 首次启动会下载 ~150MB 模型，在日志中提示
        logger.info("lancedb.loading_embedding_model", model="all-MiniLM-L6-v2")
        self._embedder = SentenceTransformer("all-MiniLM-L6-v2")

        # 创建或打开表
        try:
            self._table = self._db.open_table(self._table_name)
        except Exception:
            # 表不存在，创建（需要至少一条数据定义 schema）
            import pyarrow as pa

            schema = pa.schema(
                [
                    pa.field("id", pa.string()),
                    pa.field("doc_id", pa.string()),
                    pa.field("chunk_text", pa.string()),
                    pa.field("vector", pa.list_(pa.float32(), 384)),  # MiniLM-L6 输出 384 维
                    pa.field("metadata", pa.string()),
                ]
            )
            self._table = self._db.create_table(self._table_name, schema=schema)
            logger.info("lancedb.table_created", table=self._table_name)

        logger.info("lancedb.initialized", db_path=str(self._db_path))

    async def prefetch(self, query: str) -> list[ProviderHit]:
        """向量召回：query → embedding → ANN search → 结构化结果。"""
        if self._table is None:
            return []

        query_vec = (await asyncio.to_thread(self._embedder.encode, query)).tolist()
        results = self._table.search(query_vec).metric("cosine").limit(10).to_list()
        if not results:
            return []

        hits: list[ProviderHit] = []
        for row in results:
            hits.append(
                ProviderHit(
                    doc_id=row.get("doc_id", "unknown"),
                    content=row.get("chunk_text", "")[:500],
                    score=float(1.0 - row.get("_distance", 1.0)),  # cosine distance → similarity
                    metadata=json.loads(row.get("metadata", "{}")),
                )
            )
        return hits

    async def sync_document(
        self,
        content: str,
        doc_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """保存文档：分块 → embedding → 写入 LanceDB。"""
        if self._table is None:
            return

        chunks = self._chunk_text(content)
        if not chunks:
            return

        embeddings = (await asyncio.to_thread(self._embedder.encode, chunks)).tolist()

        rows = []
        for idx, (chunk, vec) in enumerate(zip(chunks, embeddings, strict=False)):
            chunk_id = hashlib.sha256(f"{doc_id}:{idx}:{chunk}".encode()).hexdigest()
            rows.append(
                {
                    "id": chunk_id,
                    "doc_id": doc_id,
                    "chunk_text": chunk,
                    "vector": vec,
                    "metadata": json.dumps(metadata or {}, ensure_ascii=False),
                }
            )

        with contextlib.suppress(Exception):
            self._table.delete(f"doc_id = '{doc_id}'")

        self._table.add(rows)
        logger.info("lancedb.document_synced", doc_id=doc_id, chunks=len(rows))

    async def clear(self) -> bool:
        """清空 LanceDB 表。"""
        try:
            if self._table is not None:
                self._db.drop_table(self._table_name, ignore_missing=True)
                self._table = None
            logger.info("lancedb.index_cleared", table=self._table_name)
            return True
        except Exception as exc:
            logger.error("lancedb.clear_failed", error=str(exc))
            return False

    async def delete_document(self, doc_id: str) -> bool:
        """删除指定 doc_id 的所有 chunks。"""
        if self._table is None:
            return False
        try:
            self._table.delete(f"doc_id = '{doc_id}'")
            logger.info("lancedb.document_deleted", doc_id=doc_id)
            return True
        except Exception as exc:
            logger.error("lancedb.delete_failed", doc_id=doc_id, error=str(exc))
            return False

    def _chunk_text(self, text: str, chunk_size: int = 512, overlap: int = 50) -> list[str]:
        """简单 overlap 分块。"""
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size - overlap)]

    def get_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "data_source_search",
                "description": "搜索外部数据源中的相关文档（语义搜索）",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            }
        ]

    async def handle_tool_call(self, name: str, args: dict) -> str:
        if name == "data_source_search":
            hits = await self.prefetch(args["query"])
            if not hits:
                return "未找到相关内容。"
            return "\n\n".join(f"[来源: {h.doc_id}]\n{h.content}" for h in hits)
        raise NotImplementedError(f"Tool {name} not supported")
