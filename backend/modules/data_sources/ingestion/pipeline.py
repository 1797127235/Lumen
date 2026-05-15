"""摄入管线 — 驱动 DataSourceConnector 写入 external_items FTS5 表。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from pathlib import Path

from sqlalchemy import text

from backend.core.db import get_async_session_maker
from backend.core.logging import get_logger
from backend.modules.data_sources.ingestion.connector import DataSourceConnector, RawBytes, StructuredDocument
from backend.modules.data_sources.ingestion.document_index_provider import DocumentIndexProvider
from backend.modules.data_sources.ingestion.parser import parse_raw_bytes
from backend.modules.data_sources.ingestion.retry import jittered_sleep
from backend.modules.data_sources.ingestion.store import IngestionStore

logger = get_logger(__name__)

MAX_RETRY = 3
MAX_MEMORY_RETRY = 3  # _memory_worker 重试次数，超过后记录错误放弃
MAX_CONTENT_CHARS = 150_000  # 与 local_folder.MAX_FILE_SIZE_BYTES (500KB) 对齐：CJK ~450KB, ASCII ~150KB


class IngestionPipeline:
    """协调多个 DataSourceConnector，将文档写入 external_items。

    Phase 3 改造：
      - 注入 DocumentIndexProvider，移除硬编码 Cognee
      - 批量写入（batch_size=100 或 flush_interval=5s）
      - 背压控制（memory_queue maxsize=1000）
      - 异步记忆索引队列

    状态机（每个文档）：
        discovered → parse → dedup_check → batch_buffer → DB_flush
                                                     → memory_queue
    """

    def __init__(
        self,
        store_path: Path,
        document_index_provider: DocumentIndexProvider,
        batch_size: int = 100,
        flush_interval: float = 5.0,
    ) -> None:
        self._store = IngestionStore(store_path)
        self._memory = document_index_provider
        self._connectors: list[DataSourceConnector] = []
        self._running = False

        # 批量写入缓冲
        self._batch: list[StructuredDocument] = []
        self._batch_size = batch_size
        self._flush_interval = flush_interval

        # 背压队列：maxsize=1000，队列满时 put() 挂起，对上游产生背压
        self._memory_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._memory_task: asyncio.Task | None = None
        self._flush_task: asyncio.Task | None = None

    def register(self, connector: DataSourceConnector) -> None:
        """注册一个数据源连接器。"""
        self._connectors.append(connector)

    async def start(self) -> None:
        """启动后台任务：记忆索引 worker + 定时 flush worker。"""
        if not self._running:
            self._running = True
            self._memory_task = asyncio.create_task(self._memory_worker(), name="memory-index-worker")
            self._flush_task = asyncio.create_task(self._flush_timer(), name="batch-flush-timer")
            logger.info("ingestion.pipeline.started", provider=self._memory.name)

    async def stop(self) -> None:
        """停止后台 worker，flush 剩余批次，drain 记忆队列。"""
        self._running = False

        # 先取消定时 flush task
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task

        # flush 剩余 DB batch
        if self._batch:
            await self._flush_batch()

        # drain 记忆队列：关机前把 queue 里剩余的文档 sync 掉
        drain_count = 0
        while not self._memory_queue.empty():
            try:
                doc: StructuredDocument = self._memory_queue.get_nowait()
                with contextlib.suppress(Exception):
                    await self._memory.sync_document(
                        content=doc.content[:MAX_CONTENT_CHARS],
                        doc_id=doc.external_id,
                        metadata=doc.metadata,
                    )
                drain_count += 1
            except asyncio.QueueEmpty:
                break
        if drain_count:
            logger.info("ingestion.memory_queue_drained", count=drain_count)

        # 取消 memory worker
        if self._memory_task and not self._memory_task.done():
            self._memory_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._memory_task

        logger.info("ingestion.pipeline.stopped")

    async def run_full_scan(self) -> dict[str, int]:
        """全量扫描所有已配置的连接器，返回 {source_id: indexed_count}。"""
        await self.start()
        summary: dict[str, int] = {}
        for connector in self._connectors:
            if not connector.is_configured():
                logger.info("ingestion.skip_unconfigured", source=connector.source_id)
                continue
            count, scanned_ids = await self._scan_connector(connector)
            if connector.data_source_id:
                await self.cleanup_deleted(connector.data_source_id, scanned_ids)
            summary[connector.source_id] = count
            await self._store.set_last_scan(connector.source_id)
        return summary

    async def _scan_connector(self, connector: DataSourceConnector) -> tuple[int, set[str]]:
        count = 0
        scanned_ids: set[str] = set()
        async for raw in connector.scan():
            scanned_ids.add(raw.external_id)
            ok = await self._ingest_with_retry(raw)
            if ok:
                count += 1
        logger.info("ingestion.scan_done", source=connector.source_id, indexed=count)
        return count, scanned_ids

    async def _ingest_with_retry(self, raw: RawBytes) -> bool:
        """带 jittered 重试的单文档摄入。"""
        store_key = f"{raw.data_source_id}:{raw.external_id}"
        if await self._store.is_indexed(store_key, raw.content_hash):
            return False

        try:
            doc = parse_raw_bytes(raw)
        except Exception as exc:
            logger.warning("ingestion.parse_failed", external_id=raw.external_id, error=str(exc))
            return False

        last_error = ""
        for attempt in range(1, MAX_RETRY + 1):
            try:
                await self._write_to_db(doc)
                return True
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "ingestion.write_failed",
                    external_id=doc.external_id,
                    attempt=attempt,
                    error=last_error,
                )
                if attempt < MAX_RETRY:
                    await jittered_sleep(attempt)
        await self._store.mark_failed(store_key, last_error, content_hash=raw.content_hash)
        return False

    async def _write_to_db(self, doc: StructuredDocument) -> None:
        """将文档加入批量缓冲。"""
        self._batch.append(doc)
        if len(self._batch) >= self._batch_size:
            await self._flush_batch()

    async def _flush_batch(self) -> None:
        """批量 UPSERT，单事务。DB commit 成功后才会 mark_indexed。"""
        if not self._batch:
            return

        batch = self._batch[:]
        self._batch.clear()
        try:
            async with get_async_session_maker()() as db:
                for doc in batch:
                    item_id = f"{doc.data_source_id}:{uuid.uuid5(uuid.NAMESPACE_URL, doc.external_id)}"
                    metadata_json = json.dumps(doc.metadata, ensure_ascii=False)

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
                            "source_id": doc.data_source_id,
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

            # DB flush 成功后，才 mark_indexed + 语义索引入队
            for doc in batch:
                store_key = f"{doc.data_source_id}:{doc.external_id}"
                await self._store.mark_indexed(store_key, doc.content_hash, doc.data_source_id)
                await self._memory_queue.put(doc)

            logger.info("ingestion.batch_flushed", count=len(batch))
        except Exception:
            logger.exception("ingestion.batch_flush_failed", count=len(batch))

    async def _memory_worker(self) -> None:
        """后台消费记忆队列，写入 DocumentIndexProvider。重试 MAX_MEMORY_RETRY 次。"""
        while self._running:
            try:
                doc: StructuredDocument = await self._memory_queue.get()
                last_error = ""
                for attempt in range(1, MAX_MEMORY_RETRY + 1):
                    try:
                        await self._memory.sync_document(
                            content=doc.content[:MAX_CONTENT_CHARS],
                            doc_id=doc.external_id,
                            metadata=doc.metadata,
                        )
                        break
                    except Exception as exc:
                        last_error = str(exc)
                        logger.warning(
                            "ingestion.memory_sync_failed",
                            doc_id=doc.external_id,
                            attempt=attempt,
                            error=last_error,
                        )
                        if attempt < MAX_MEMORY_RETRY:
                            await jittered_sleep(attempt)
                else:
                    # 重试耗尽，该文档永久丢失语义索引
                    logger.error(
                        "ingestion.memory_sync_permanent_failure",
                        doc_id=doc.external_id,
                        retries=MAX_MEMORY_RETRY,
                        error=last_error,
                    )
            except asyncio.CancelledError:
                break

    async def _flush_timer(self) -> None:
        """定时 flush batch：每 flush_interval 秒检查一次，有数据就刷盘。"""
        while self._running:
            try:
                await asyncio.sleep(self._flush_interval)
                if self._batch:
                    await self._flush_batch()
            except asyncio.CancelledError:
                break

    async def handle_change(self, raw: RawBytes) -> None:
        """文件监听回调：单个文件变更时调用。"""
        logger.info("ingestion.file_changed", external_id=raw.external_id)
        await self._ingest_with_retry(raw)

    async def handle_delete(self, data_source_id: str, external_id: str) -> None:
        """文件删除回调：从 external_items、store、语义索引中移除。"""
        async with get_async_session_maker()() as db:
            await db.execute(
                text("DELETE FROM external_items WHERE data_source_id=:dsid AND external_id=:eid"),
                {"dsid": data_source_id, "eid": external_id},
            )
            await db.commit()
        store_key = f"{data_source_id}:{external_id}"
        await self._store.remove(store_key)
        await self._memory.delete_document(external_id)
        logger.info("ingestion.deleted", data_source_id=data_source_id, external_id=external_id)

    async def cleanup_deleted(self, data_source_id: str, existing_ids: set[str]) -> int:
        """对比已索引文件和传入的 existing_ids，删除已不存在的条目。"""
        indexed_ids = await self._store.get_indexed_ids(data_source_id)
        if not indexed_ids:
            return 0

        to_delete = indexed_ids - existing_ids
        if not to_delete:
            return 0

        deleted = 0
        async with get_async_session_maker()() as db:
            for ext_id in to_delete:
                result = await db.execute(
                    text("DELETE FROM external_items WHERE data_source_id = :dsid AND external_id = :eid"),
                    {"dsid": data_source_id, "eid": ext_id},
                )
                deleted += result.rowcount
            await db.commit()

        for ext_id in to_delete:
            await self._store.remove(f"{data_source_id}:{ext_id}")
            await self._memory.delete_document(ext_id)

        logger.info("ingestion.cleanup_deleted", data_source_id=data_source_id, count=deleted)
        return deleted

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


def get_document_index_provider() -> DocumentIndexProvider | None:
    """获取当前 Pipeline 使用的 DocumentIndexProvider（安全，未初始化时返回 None）。"""
    global _pipeline
    if _pipeline is None:
        return None
    return _pipeline._memory


def init_pipeline(
    store_dir: Path,
    document_index_provider: DocumentIndexProvider | None = None,
) -> IngestionPipeline:
    global _pipeline
    from backend.modules.data_sources.ingestion.provider_factory import create_document_index_provider

    if document_index_provider is None:
        document_index_provider = create_document_index_provider()

    _pipeline = IngestionPipeline(
        store_dir / "ingestion_state.json",
        document_index_provider=document_index_provider,
    )
    return _pipeline
