"""KbStore 的 SQLite 实现：知识库文档元数据 CRUD。"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class KbDocument:
    """知识库文档。"""

    id: str
    name: str
    path: str | None
    checksum: str | None
    chunks_count: int
    status: Literal["processing", "ready", "failed"]
    created_at: str


@dataclass
class KbChunk:
    """知识库分块。"""

    id: str
    document_id: str
    text: str
    chunk_index: int
    token_count: int


class SQLiteKbStore:
    """知识库文档存储。"""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def create_kb_document(self, name: str, path: str | None, checksum: str | None) -> KbDocument:
        """创建文档记录。"""
        doc_id = str(uuid.uuid4())
        await self._db.execute(
            """INSERT INTO kb_documents (id, name, path, checksum, chunks_count, status)
               VALUES (?, ?, ?, ?, 0, 'processing')""",
            (doc_id, name, path, checksum),
        )
        await self._db.commit()
        return await self.get_kb_document(doc_id)  # type: ignore

    async def get_kb_document(self, doc_id: str) -> KbDocument | None:
        """获取文档。"""
        cursor = await self._db.execute(
            """SELECT id, name, path, checksum, chunks_count, status, created_at
               FROM kb_documents WHERE id = ?""",
            (doc_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return KbDocument(
            id=row["id"],
            name=row["name"],
            path=row["path"],
            checksum=row["checksum"],
            chunks_count=row["chunks_count"],
            status=row["status"],
            created_at=row["created_at"],
        )

    async def get_kb_documents(self, doc_ids: list[str]) -> list[KbDocument]:
        """批量获取文档。"""
        if not doc_ids:
            return []
        placeholders = ",".join(["?"] * len(doc_ids))
        cursor = await self._db.execute(
            f"""SELECT id, name, path, checksum, chunks_count, status, created_at
                FROM kb_documents WHERE id IN ({placeholders})""",
            doc_ids,
        )
        rows = await cursor.fetchall()
        return [
            KbDocument(
                id=row["id"],
                name=row["name"],
                path=row["path"],
                checksum=row["checksum"],
                chunks_count=row["chunks_count"],
                status=row["status"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def list_kb_documents(self) -> list[KbDocument]:
        """列出所有文档。"""
        cursor = await self._db.execute(
            """SELECT id, name, path, checksum, chunks_count, status, created_at
               FROM kb_documents ORDER BY created_at DESC"""
        )
        rows = await cursor.fetchall()
        return [
            KbDocument(
                id=row["id"],
                name=row["name"],
                path=row["path"],
                checksum=row["checksum"],
                chunks_count=row["chunks_count"],
                status=row["status"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def get_chunks_by_indices(self, document_id: str, chunk_indices: list[int]) -> list[KbChunk]:
        """获取文档指定索引的分块。"""
        if not chunk_indices:
            return []
        placeholders = ",".join(["?"] * len(chunk_indices))
        cursor = await self._db.execute(
            f"""SELECT id, document_id, text, chunk_index, token_count
                FROM kb_chunks
                WHERE document_id = ? AND chunk_index IN ({placeholders})
                ORDER BY chunk_index""",
            [document_id, *chunk_indices],
        )
        rows = await cursor.fetchall()
        return [
            KbChunk(
                id=row["id"],
                document_id=row["document_id"],
                text=row["text"],
                chunk_index=row["chunk_index"],
                token_count=row["token_count"],
            )
            for row in rows
        ]

    async def get_file_chunks(self, document_id: str, offset: int = 0, limit: int = 10) -> list[KbChunk]:
        """获取文档的分块列表（分页）。"""
        cursor = await self._db.execute(
            """SELECT id, document_id, text, chunk_index, token_count
               FROM kb_chunks
               WHERE document_id = ?
               ORDER BY chunk_index
               LIMIT ? OFFSET ?""",
            (document_id, limit, offset),
        )
        rows = await cursor.fetchall()
        return [
            KbChunk(
                id=row["id"],
                document_id=row["document_id"],
                text=row["text"],
                chunk_index=row["chunk_index"],
                token_count=row["token_count"],
            )
            for row in rows
        ]

    async def get_chunk(self, chunk_id: str) -> KbChunk | None:
        """获取单个分块。"""
        cursor = await self._db.execute(
            """SELECT id, document_id, text, chunk_index, token_count
               FROM kb_chunks WHERE id = ?""",
            (chunk_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return KbChunk(
            id=row["id"],
            document_id=row["document_id"],
            text=row["text"],
            chunk_index=row["chunk_index"],
            token_count=row["token_count"],
        )

    async def set_kb_document_status(self, doc_id: str, status: str, chunks_count: int | None = None) -> None:
        """更新文档状态。"""
        if chunks_count is not None:
            await self._db.execute(
                "UPDATE kb_documents SET status = ?, chunks_count = ? WHERE id = ?",
                (status, chunks_count, doc_id),
            )
        else:
            await self._db.execute("UPDATE kb_documents SET status = ? WHERE id = ?", (status, doc_id))
        await self._db.commit()

    async def delete_kb_document(self, doc_id: str) -> None:
        """删除文档及其分块。"""
        await self._db.execute("DELETE FROM kb_documents WHERE id = ?", (doc_id,))
        await self._db.commit()

    async def count_chunks(self) -> int:
        """统计分块总数。"""
        cursor = await self._db.execute("SELECT COUNT(*) AS c FROM kb_chunks")
        row = await cursor.fetchone()
        return row["c"] if row else 0
