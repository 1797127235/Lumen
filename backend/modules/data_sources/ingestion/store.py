"""摄入状态 store — 全量走 SQLite，JSON 双写已移除。

所有状态读写走 ingestion_state 表（进程安全、事务一致）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select

from backend.core.db import get_async_session_maker
from backend.core.logging import get_logger
from backend.modules.data_sources.models import IngestionState

logger = get_logger(__name__)

# 超过此重试次数后标记为永久失败，不再重试
MAX_PERMANENT_FAILURE_COUNT = 9


class IngestionStore:
    """追踪外部文档的索引状态（SQLite 主存储）。"""

    def __init__(self, path: Path) -> None:
        # path 参数保留以兼容调用方，不再用于 JSON 写入
        self.path = path

    # ── 主读写（async）──

    async def is_indexed(self, doc_id: str, content_hash: str) -> bool:
        data_source_id, external_id = self._parse_key(doc_id)
        async with get_async_session_maker()() as db:
            # 已成功索引且 hash 相同 → 跳过
            result = await db.execute(
                select(IngestionState).where(
                    IngestionState.data_source_id == data_source_id,
                    IngestionState.external_id == external_id,
                    IngestionState.content_hash == content_hash,
                    IngestionState.status == "indexed",
                )
            )
            if result.scalar_one_or_none() is not None:
                return True

            # 永久失败（超过最大重试次数）→ 也跳过
            # 注意：mark_failed 写入的是实际 content_hash，不再用空字符串
            result = await db.execute(
                select(IngestionState).where(
                    IngestionState.data_source_id == data_source_id,
                    IngestionState.external_id == external_id,
                    IngestionState.content_hash == content_hash,
                    IngestionState.status == "failed",
                    IngestionState.retry_count >= MAX_PERMANENT_FAILURE_COUNT,
                )
            )
            return result.scalar_one_or_none() is not None

    async def mark_indexed(self, doc_id: str, content_hash: str, source_id: str) -> None:
        data_source_id, external_id = self._parse_key(doc_id)
        now = datetime.now(UTC)
        async with get_async_session_maker()() as db:
            from sqlalchemy.dialects.sqlite import insert

            stmt = insert(IngestionState).values(
                data_source_id=data_source_id,
                external_id=external_id,
                content_hash=content_hash,
                status="indexed",
                error_message=None,
                retry_count=0,
                indexed_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["data_source_id", "external_id"],
                set_={
                    "content_hash": content_hash,
                    "status": "indexed",
                    "error_message": None,
                    "retry_count": 0,
                    "indexed_at": now,
                },
            )
            await db.execute(stmt)
            await db.commit()

    async def mark_failed(self, doc_id: str, reason: str, content_hash: str = "") -> None:
        """标记文档索引失败，记录实际 content_hash 以便永久失败判断正确匹配。"""
        data_source_id, external_id = self._parse_key(doc_id)
        async with get_async_session_maker()() as db:
            from sqlalchemy.dialects.sqlite import insert

            stmt = insert(IngestionState).values(
                data_source_id=data_source_id,
                external_id=external_id,
                content_hash=content_hash,
                status="failed",
                error_message=reason[:500],
                retry_count=1,
                indexed_at=None,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["data_source_id", "external_id"],
                set_={
                    "content_hash": content_hash,
                    "status": "failed",
                    "error_message": reason[:500],
                    "retry_count": IngestionState.retry_count + 1,
                },
            )
            await db.execute(stmt)
            await db.commit()

    async def get_retry_count(self, doc_id: str) -> int:
        data_source_id, external_id = self._parse_key(doc_id)
        async with get_async_session_maker()() as db:
            result = await db.execute(
                select(IngestionState.retry_count).where(
                    IngestionState.data_source_id == data_source_id,
                    IngestionState.external_id == external_id,
                )
            )
            row = result.scalar_one_or_none()
            return row or 0

    async def set_last_scan(self, source_id: str) -> None:
        """记录最近一次扫描时间（写入 data_sources.last_sync_at，此处为空操作占位）。

        last_sync_at 由 service.trigger_sync 在 DataSource 行上更新，
        IngestionStore 不再维护独立的扫描时间记录。
        """

    async def remove(self, doc_id: str) -> None:
        """删除指定文档的状态（用于 handle_delete）。"""
        data_source_id, external_id = self._parse_key(doc_id)
        async with get_async_session_maker()() as db:
            await db.execute(
                delete(IngestionState).where(
                    IngestionState.data_source_id == data_source_id,
                    IngestionState.external_id == external_id,
                )
            )
            await db.commit()

    async def get_indexed_ids(self, data_source_id: str) -> set[str]:
        """获取指定数据源下所有已索引的 external_id。"""
        async with get_async_session_maker()() as db:
            result = await db.execute(
                select(IngestionState.external_id).where(
                    IngestionState.data_source_id == data_source_id,
                    IngestionState.status == "indexed",
                )
            )
            return {row[0] for row in result.fetchall()}

    # ── 内部 helpers ──

    def _parse_key(self, doc_id: str) -> tuple[str, str]:
        """store_key (ds_id:external_id) → (data_source_id, external_id)。"""
        if ":" in doc_id:
            parts = doc_id.split(":", 1)
            return parts[0], parts[1]
        return "legacy", doc_id
