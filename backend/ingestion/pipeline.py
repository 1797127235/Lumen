"""摄入管线 — 驱动 DataSourceConnector 写入 external_items FTS5 表。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import text

from backend.db import get_async_session_maker
from backend.ingestion.connector import DataSourceConnector, RawDocument
from backend.ingestion.retry import jittered_sleep
from backend.ingestion.store import IngestionStore
from backend.logging_config import get_logger

logger = get_logger(__name__)

MAX_RETRY = 3


class IngestionPipeline:
    """协调多个 DataSourceConnector，将文档写入 external_items。

    状态机（每个文档）：
        discovered → dedup_check → indexing → completed
                                             → failed → retrying
    """

    def __init__(self, store_path: Path) -> None:
        self._store = IngestionStore(store_path)
        self._connectors: list[DataSourceConnector] = []
        self._running = False

    def register(self, connector: DataSourceConnector) -> None:
        """注册一个数据源连接器。"""
        self._connectors.append(connector)

    async def run_full_scan(self) -> dict[str, int]:
        """全量扫描所有已配置的连接器，返回 {source_id: indexed_count}。"""
        summary: dict[str, int] = {}
        for connector in self._connectors:
            if not connector.is_configured():
                logger.info("ingestion.skip_unconfigured", source=connector.source_id)
                continue
            count = await self._scan_connector(connector)
            summary[connector.source_id] = count
            self._store.set_last_scan(connector.source_id)
        return summary

    async def _scan_connector(self, connector: DataSourceConnector) -> int:
        count = 0
        async for doc in connector.scan():
            ok = await self._ingest_with_retry(doc)
            if ok:
                count += 1
        logger.info("ingestion.scan_done", source=connector.source_id, indexed=count)
        return count

    async def _ingest_with_retry(self, doc: RawDocument) -> bool:
        """带 jittered 重试的单文档摄入。"""
        if self._store.is_indexed(doc.external_id, doc.content_hash):
            return False

        for attempt in range(1, MAX_RETRY + 1):
            try:
                await self._write_to_db(doc)
                self._store.mark_indexed(doc.external_id, doc.content_hash, doc.connector_type)
                return True
            except Exception as exc:
                logger.warning(
                    "ingestion.write_failed",
                    external_id=doc.external_id,
                    attempt=attempt,
                    error=str(exc),
                )
                self._store.mark_failed(doc.external_id, str(exc))
                if attempt < MAX_RETRY:
                    await jittered_sleep(attempt)
        return False

    async def _write_to_db(self, doc: RawDocument) -> None:
        """UPSERT 文档到 external_items。FTS5 由 SQLite trigger 同步。"""
        item_id = f"{doc.connector_type}:{uuid.uuid5(uuid.NAMESPACE_URL, doc.external_id)}"
        metadata_json = json.dumps(doc.metadata, ensure_ascii=False)

        async with get_async_session_maker()() as db:
            # UPSERT（ON CONFLICT 更新内容）
            await db.execute(
                text("""
                INSERT INTO external_items (
                    id, user_id, data_source_id, connector_type, source_id, doc_id, external_id,
                    uri, title, content, content_hash, metadata_json, indexed_at, updated_at
                )
                VALUES (
                    :id, :user_id, :ds_id, :ctype, :source_id, :doc_id, :ext_id,
                    :uri, :title, :content, :hash, :meta, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT(data_source_id, external_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    data_source_id = excluded.data_source_id,
                    connector_type = excluded.connector_type,
                    external_id = excluded.external_id,
                    uri = excluded.uri,
                    title = excluded.title,
                    content = excluded.content,
                    content_hash = excluded.content_hash,
                    metadata_json = excluded.metadata_json,
                    indexed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP,
                    deleted_at = NULL
            """),
                {
                    "id": item_id,
                    "user_id": doc.user_id,
                    "ds_id": doc.data_source_id,
                    "ctype": doc.connector_type,
                    "source_id": doc.connector_type,
                    "doc_id": doc.external_id,
                    "ext_id": doc.external_id,
                    "uri": doc.uri,
                    "title": doc.title,
                    "content": doc.content[:50000],
                    "hash": doc.content_hash,
                    "meta": metadata_json,
                },
            )
            await db.commit()

    async def handle_change(self, doc: RawDocument) -> None:
        """文件监听回调：单个文件变更时调用。"""
        logger.info("ingestion.file_changed", external_id=doc.external_id)
        await self._ingest_with_retry(doc)

    async def handle_delete(self, data_source_id: str, external_id: str) -> None:
        """文件删除回调：从 external_items 移除并清理 store。"""
        async with get_async_session_maker()() as db:
            await db.execute(
                text("DELETE FROM external_items WHERE data_source_id=:dsid AND external_id=:eid"),
                {"dsid": data_source_id, "eid": external_id},
            )
            await db.commit()
        with self._store._lock:
            self._store._state["indexed"].pop(external_id, None)
            self._store._state["failed"].pop(external_id, None)
            self._store._save()
        logger.info("ingestion.deleted", data_source_id=data_source_id, external_id=external_id)

    def start_watching_all(self) -> None:
        """启动所有连接器的增量监听。loop 在主线程获取，显式传入 watchdog 线程。"""
        import asyncio

        loop = asyncio.get_running_loop()
        for connector in self._connectors:
            if connector.is_configured():
                connector.start_watching(self.handle_change, self.handle_delete, loop=loop)

    def stop_watching_all(self) -> None:
        for connector in self._connectors:
            connector.stop_watching()


# 全局单例
_pipeline: IngestionPipeline | None = None


def get_pipeline() -> IngestionPipeline:
    global _pipeline
    assert _pipeline is not None, "IngestionPipeline 未初始化"
    return _pipeline


def init_pipeline(store_dir: Path) -> IngestionPipeline:
    global _pipeline
    _pipeline = IngestionPipeline(store_dir / "ingestion_state.json")
    return _pipeline
