"""DataSource 业务逻辑 — CRUD + 测试连接 + 触发同步。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.logging import get_logger
from backend.modules.data_sources.models import DataSource
from backend.modules.data_sources.registry import create_connector

logger = get_logger(__name__)


# ── CRUD ──


async def create_data_source(
    db: AsyncSession,
    user_id: str,
    name: str,
    type: str,
    config: dict[str, Any],
) -> DataSource:
    """创建数据源连接。"""
    capabilities = _infer_capabilities(type)
    ds = DataSource(
        user_id=user_id,
        name=name,
        type=type,
        status="active",
        config_json=config,
        capabilities_json=capabilities,
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    logger.info("data_source.created", source_id=ds.id, type=type, user_id=user_id)
    return ds


async def list_data_sources(
    db: AsyncSession,
    user_id: str,
    *,
    status: str | None = None,
) -> list[DataSource]:
    """列出用户的数据源。"""
    stmt = select(DataSource).where(DataSource.user_id == user_id)
    if status:
        stmt = stmt.where(DataSource.status == status)
    stmt = stmt.order_by(DataSource.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_data_source(db: AsyncSession, user_id: str, source_id: str) -> DataSource | None:
    """按 ID 获取数据源。"""
    stmt = select(DataSource).where(
        DataSource.id == source_id,
        DataSource.user_id == user_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def update_data_source(
    db: AsyncSession,
    user_id: str,
    source_id: str,
    *,
    name: str | None = None,
    status: str | None = None,
    config: dict[str, Any] | None = None,
) -> DataSource | None:
    """更新数据源。"""
    ds = await get_data_source(db, user_id, source_id)
    if ds is None:
        return None

    if name is not None:
        ds.name = name
    if status is not None:
        ds.status = status
    if config is not None:
        ds.config_json = config
    ds.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(ds)
    logger.info("data_source.updated", source_id=source_id, status=status)
    return ds


async def delete_data_source(db: AsyncSession, user_id: str, source_id: str) -> bool:
    """删除数据源及其关联 external_items。"""
    ds = await get_data_source(db, user_id, source_id)
    if ds is None:
        return False

    # 软删除关联的 external_items
    from sqlalchemy import text

    await db.execute(
        text("UPDATE external_items SET deleted_at = CURRENT_TIMESTAMP WHERE data_source_id = :dsid"),
        {"dsid": source_id},
    )

    await db.delete(ds)
    await db.commit()
    logger.info("data_source.deleted", source_id=source_id, user_id=user_id)
    return True


# ── 业务操作 ──


async def test_connection(db: AsyncSession, user_id: str, source_id: str) -> dict[str, Any]:
    """测试数据源连接是否可用。"""
    ds = await get_data_source(db, user_id, source_id)
    if ds is None:
        return {"ok": False, "error": "数据源不存在"}

    connector = create_connector(ds)
    if connector is None:
        return {"ok": False, "error": f"不支持的连接器类型: {ds.type}"}

    ok = connector.is_configured()
    return {
        "ok": ok,
        "error": None if ok else "连接配置无效（目录不存在或路径错误）",
        "type": ds.type,
        "capabilities": ds.capabilities_json,
    }


async def trigger_sync(
    db: AsyncSession,
    user_id: str,
    source_id: str,
    pipeline,
) -> dict[str, Any]:
    """触发单数据源的同步。"""
    ds = await get_data_source(db, user_id, source_id)
    if ds is None:
        return {"ok": False, "error": "数据源不存在"}

    if ds.status != "active":
        return {"ok": False, "error": f"数据源状态为 {ds.status}，无法同步"}

    connector = create_connector(ds)
    if connector is None:
        return {"ok": False, "error": f"不支持的连接器类型: {ds.type}"}

    # 执行同步
    try:
        count = 0
        scanned_ids: set[str] = set()
        async for doc in connector.scan():
            scanned_ids.add(doc.external_id)
            ok = await pipeline._ingest_with_retry(doc)
            if ok:
                count += 1

        # 清理已从文件系统删除的文件
        cleanup_count = await pipeline.cleanup_deleted(source_id, scanned_ids)

        ds.last_sync_at = datetime.now(UTC)
        ds.last_error = None
        await db.commit()
        logger.info("data_source.synced", source_id=source_id, indexed=count, cleaned=cleanup_count)
        return {"ok": True, "indexed": count, "cleaned": cleanup_count}
    except Exception as exc:
        ds.last_error = str(exc)[:500]
        await db.commit()
        logger.warning("data_source.sync_failed", source_id=source_id, error=str(exc))
        return {"ok": False, "error": str(exc)[:500]}


# ── 辅助 ──


def _infer_capabilities(type: str) -> list[str]:
    """根据类型推断能力。"""
    caps: dict[str, list[str]] = {
        "local_folder": ["scan", "watch"],
        "web_url": ["scan"],
        "github_repo": ["scan", "incremental"],
    }
    return caps.get(type, ["scan"])
