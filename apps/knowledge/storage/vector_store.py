"""VectorStore 的 SQLite 实现：embedding 存为 Float32 BLOB，余弦检索。"""

from __future__ import annotations

import logging
import struct
import uuid
from dataclasses import dataclass

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class ChunkInput:
    """分块输入。"""

    text: str
    embedding: list[float]
    index: int
    token_count: int


@dataclass
class RetrievalHit:
    """检索结果。"""

    chunk_id: str
    document_id: str
    text: str
    score: float
    document_name: str


def vector_to_buffer(vec: list[float]) -> bytes:
    """向量转 bytes (Float32)。"""
    return struct.pack(f"{len(vec)}f", *vec)


def buffer_to_vector(buf: bytes) -> list[float]:
    """bytes 转向量 (Float32)。"""
    n = len(buf) // 4
    return list(struct.unpack(f"{n}f", buf))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度。"""
    if len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SQLiteVectorStore:
    """SQLite 向量存储。"""

    def __init__(self, db: aiosqlite.Connection, dimension: int = 1024) -> None:
        self._db = db
        self.dimension = dimension

    async def insert_chunks(self, document_id: str, chunks: list[ChunkInput]) -> None:
        """批量写入分块向量。"""
        for chunk in chunks:
            await self._db.execute(
                """INSERT INTO kb_chunks (id, document_id, text, embedding, chunk_index, token_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    document_id,
                    chunk.text,
                    vector_to_buffer(chunk.embedding),
                    chunk.index,
                    chunk.token_count,
                ),
            )
        await self._db.commit()

    async def retrieve(self, query_vec: list[float], top_k: int = 4) -> list[RetrievalHit]:
        """基于查询向量检索 Top-K。"""
        cursor = await self._db.execute(
            """SELECT c.id AS chunk_id, c.document_id, c.text, c.embedding, d.name AS document_name
               FROM kb_chunks c
               JOIN kb_documents d ON d.id = c.document_id
               WHERE d.status = 'ready'
               ORDER BY c.document_id, c.chunk_index"""
        )
        rows = await cursor.fetchall()

        if not rows:
            return []

        scored: list[RetrievalHit] = []
        for row in rows:
            vec = buffer_to_vector(row["embedding"])
            score = cosine_similarity(query_vec, vec)
            scored.append(
                RetrievalHit(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    text=row["text"],
                    score=score,
                    document_name=row["document_name"],
                )
            )

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]
