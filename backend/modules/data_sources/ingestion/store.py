"""摄入状态 store — DB 为主，JSON 为备份与降级。

Phase 1 迁移完成后，所有状态读写走 SQLite（进程安全、事务一致）。
JSON 文件保留为备份，支持一键回滚。
"""

from __future__ import annotations

import contextlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from sqlalchemy import delete, select

from backend.core.db import get_async_session_maker
from backend.core.logging import get_logger
from backend.modules.data_sources.models import IngestionState

logger = get_logger(__name__)


class IngestionStore:
    """追踪外部文档的索引状态。

    实现策略：
      - 主存储：SQLite ORM（IngestionState 表），进程安全，与 external_items 同事务。
      - 备份：JSON 文件，在 DB 写入成功后同步更新，用于回滚和降级。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._backup_path = path.with_suffix(".json.backup")
        self._legacy_path = path  # 兼容旧路径
        self._state: dict[str, Any] | None = None
        if path.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                self._state = json.loads(path.read_text(encoding="utf-8"))

    # ── 内部 helpers ──

    def _parse_key(self, doc_id: str) -> tuple[str, str]:
        """store_key (ds_id:external_id) → (data_source_id, external_id)。"""
        if ":" in doc_id:
            parts = doc_id.split(":", 1)
            return parts[0], parts[1]
        # 兼容旧格式：无冒号时整个当 external_id，data_source_id 为 legacy
        return "legacy", doc_id

    # ── DB 主读写（async）──

    async def is_indexed(self, doc_id: str, content_hash: str) -> bool:
        data_source_id, external_id = self._parse_key(doc_id)
        async with get_async_session_maker()() as db:
            result = await db.execute(
                select(IngestionState).where(
                    IngestionState.data_source_id == data_source_id,
                    IngestionState.external_id == external_id,
                    IngestionState.content_hash == content_hash,
                    IngestionState.status == "indexed",
                )
            )
            return result.scalar_one_or_none() is not None

    async def mark_indexed(self, doc_id: str, content_hash: str, source_id: str) -> None:
        data_source_id, external_id = self._parse_key(doc_id)
        now = datetime.now(UTC)
        async with get_async_session_maker()() as db:
            # UPSERT：SQLite 3.24+ 支持 ON CONFLICT
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

        # 同步更新备份 JSON
        self._sync_backup_indexed(doc_id, content_hash, source_id)

    async def mark_failed(self, doc_id: str, reason: str) -> None:
        data_source_id, external_id = self._parse_key(doc_id)
        async with get_async_session_maker()() as db:
            from sqlalchemy.dialects.sqlite import insert

            stmt = insert(IngestionState).values(
                data_source_id=data_source_id,
                external_id=external_id,
                content_hash="",
                status="failed",
                error_message=reason[:500],
                retry_count=1,
                indexed_at=None,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["data_source_id", "external_id"],
                set_={
                    "status": "failed",
                    "error_message": reason[:500],
                    "retry_count": IngestionState.retry_count + 1,
                },
            )
            await db.execute(stmt)
            await db.commit()

        # 同步更新备份 JSON
        self._sync_backup_failed(doc_id, reason)

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
        """记录最近一次扫描时间（存入 JSON 备份即可，不单独建表）。"""
        # 此字段低频且非核心，继续使用 JSON 备份
        if self._state is None:
            self._state = {"indexed": {}, "failed": {}, "last_scan": {}}
        self._state["last_scan"][source_id] = datetime.now(UTC).isoformat()
        self._save_json()

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

        # 同步清理备份 JSON
        if self._state:
            self._state["indexed"].pop(doc_id, None)
            self._state["indexed"].pop(external_id, None)
            self._state["failed"].pop(doc_id, None)
            self._state["failed"].pop(external_id, None)
            self._save_json()

    # ── 批量查询（用于 cleanup_deleted）──

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

    # ── JSON 备份与回滚 ──

    def _sync_backup_indexed(self, doc_id: str, content_hash: str, source_id: str) -> None:
        if self._state is None:
            self._state = {"indexed": {}, "failed": {}, "last_scan": {}}
        self._state["indexed"][doc_id] = {
            "hash": content_hash,
            "indexed_at": datetime.now(UTC).isoformat(),
            "source_id": source_id,
        }
        self._state["failed"].pop(doc_id, None)
        self._save_json()

    def _sync_backup_failed(self, doc_id: str, reason: str) -> None:
        if self._state is None:
            self._state = {"indexed": {}, "failed": {}, "last_scan": {}}
        entry = self._state["failed"].get(doc_id, {"retry_count": 0})
        entry["reason"] = reason
        entry["retry_count"] = entry.get("retry_count", 0) + 1
        entry["last_attempt"] = datetime.now(UTC).isoformat()
        self._state["failed"][doc_id] = entry
        self._save_json()

    def _save_json(self) -> None:
        """原子写入 JSON 备份。"""
        if not self.path.parent.exists():
            return
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            suffix=".tmp",
            delete=False,
        ) as tmp:
            json.dump(self._state, tmp, ensure_ascii=False, indent=2)
            tmp_path = Path(tmp.name)
        tmp_path.replace(self.path)

    # ── 迁移与回滚 ──

    async def migrate_from_json(self) -> dict[str, int]:
        """将旧 JSON 状态迁移到 DB。返回统计信息 {"migrated": N, "skipped": N}。"""
        if not self._state:
            return {"migrated": 0, "skipped": 0}

        migrated = 0
        skipped = 0
        now = datetime.now(UTC)

        async with get_async_session_maker()() as db:
            for doc_id, entry in self._state.get("indexed", {}).items():
                data_source_id, external_id = self._parse_key(doc_id)
                try:
                    from sqlalchemy.dialects.sqlite import insert

                    stmt = insert(IngestionState).values(
                        data_source_id=data_source_id,
                        external_id=external_id,
                        content_hash=entry.get("hash", ""),
                        status="indexed",
                        error_message=None,
                        retry_count=0,
                        indexed_at=now,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["data_source_id", "external_id"],
                        set_={
                            "content_hash": entry.get("hash", ""),
                            "status": "indexed",
                            "indexed_at": now,
                        },
                    )
                    await db.execute(stmt)
                    migrated += 1
                except Exception as exc:
                    logger.warning("migrate.indexed_failed", doc_id=doc_id, error=str(exc))
                    skipped += 1

            for doc_id, entry in self._state.get("failed", {}).items():
                data_source_id, external_id = self._parse_key(doc_id)
                try:
                    from sqlalchemy.dialects.sqlite import insert

                    stmt = insert(IngestionState).values(
                        data_source_id=data_source_id,
                        external_id=external_id,
                        content_hash="",
                        status="failed",
                        error_message=entry.get("reason", "")[:500],
                        retry_count=entry.get("retry_count", 0),
                        indexed_at=None,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["data_source_id", "external_id"],
                        set_={
                            "status": "failed",
                            "error_message": entry.get("reason", "")[:500],
                            "retry_count": entry.get("retry_count", 0),
                        },
                    )
                    await db.execute(stmt)
                    migrated += 1
                except Exception as exc:
                    logger.warning("migrate.failed_failed", doc_id=doc_id, error=str(exc))
                    skipped += 1

            await db.commit()

        # 迁移成功后，备份旧 JSON
        if self.path.exists():
            backup = self.path.with_suffix(".json.migrated")
            shutil.copy2(self.path, backup)
            logger.info("migrate.json_backed_up", backup=str(backup))

        return {"migrated": migrated, "skipped": skipped}

    async def rollback(self) -> bool:
        """回滚：从备份 JSON 恢复状态，清空 DB 表。"""
        backup = self.path.with_suffix(".json.migrated")
        if not backup.exists():
            logger.warning("rollback.no_backup_found")
            return False

        try:
            self._state = json.loads(backup.read_text(encoding="utf-8"))
            self._save_json()

            async with get_async_session_maker()() as db:
                await db.execute(delete(IngestionState))
                await db.commit()

            logger.info("rollback.completed")
            return True
        except Exception as exc:
            logger.error("rollback.failed", error=str(exc))
            return False

    async def verify_migration(self) -> dict[str, Any]:
        """校验迁移一致性：对比 JSON 和 DB 中的条目数。"""
        if not self._state:
            return {"json_total": 0, "db_total": 0, "consistent": True}

        json_total = len(self._state.get("indexed", {})) + len(self._state.get("failed", {}))

        async with get_async_session_maker()() as db:
            from sqlalchemy import func as sa_func

            result = await db.execute(select(sa_func.count(IngestionState.data_source_id)))
            db_total = result.scalar() or 0

        return {
            "json_total": json_total,
            "db_total": db_total,
            "consistent": json_total == db_total,
        }
