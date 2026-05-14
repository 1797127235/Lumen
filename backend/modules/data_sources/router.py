"""/api/data-sources 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_db
from backend.modules.data_sources.ingestion import get_pipeline
from backend.modules.data_sources.schemas import DataSourceCreate, DataSourceRead, DataSourceUpdate
from backend.modules.data_sources.service import (
    create_data_source,
    delete_data_source,
    get_data_source,
    list_data_sources,
    test_connection,
    trigger_sync,
    update_data_source,
)

router = APIRouter(prefix="/data-sources", tags=["data-sources"])

DEFAULT_USER_ID = "demo_user"


@router.get("", response_model=list[DataSourceRead])
async def list_sources(
    user_id: str = DEFAULT_USER_ID,
    db: AsyncSession = Depends(get_db),
) -> list[Any]:
    """列出用户的数据源。"""
    sources = await list_data_sources(db, user_id)
    return [_to_read(s) for s in sources]


@router.post("", response_model=DataSourceRead, status_code=201)
async def create_source(
    body: DataSourceCreate,
    user_id: str = DEFAULT_USER_ID,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """创建数据源。"""
    ds = await create_data_source(
        db,
        user_id=user_id,
        name=body.name,
        type=body.type,
        config=body.config,
    )
    return _to_read(ds)


@router.get("/{source_id}", response_model=DataSourceRead)
async def get_source(
    source_id: str,
    user_id: str = DEFAULT_USER_ID,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """获取单个数据源。"""
    ds = await get_data_source(db, user_id, source_id)
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return _to_read(ds)


@router.patch("/{source_id}", response_model=DataSourceRead)
async def patch_source(
    source_id: str,
    body: DataSourceUpdate,
    user_id: str = DEFAULT_USER_ID,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """更新数据源。"""
    ds = await update_data_source(
        db,
        user_id,
        source_id,
        name=body.name,
        status=body.status,
        config=body.config,
    )
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return _to_read(ds)


@router.delete("/{source_id}", status_code=204)
async def remove_source(
    source_id: str,
    user_id: str = DEFAULT_USER_ID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """删除数据源。"""
    ok = await delete_data_source(db, user_id, source_id)
    if not ok:
        raise HTTPException(status_code=404, detail="数据源不存在")


@router.post("/{source_id}/test")
async def test_source(
    source_id: str,
    user_id: str = DEFAULT_USER_ID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """测试数据源连接。"""
    result = await test_connection(db, user_id, source_id)
    if not result["ok"] and result.get("error") == "数据源不存在":
        raise HTTPException(status_code=404, detail="数据源不存在")
    return result


@router.post("/{source_id}/sync")
async def sync_source(
    source_id: str,
    user_id: str = DEFAULT_USER_ID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """手动触发同步。"""
    try:
        pipeline = get_pipeline()
    except AssertionError:
        raise HTTPException(status_code=503, detail="摄入管线未初始化")

    result = await trigger_sync(db, user_id, source_id, pipeline)
    if not result["ok"] and result.get("error") == "数据源不存在":
        raise HTTPException(status_code=404, detail="数据源不存在")
    return result


@router.post("/{source_id}/pause", response_model=DataSourceRead)
async def pause_source(
    source_id: str,
    user_id: str = DEFAULT_USER_ID,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """暂停数据源。"""
    ds = await update_data_source(db, user_id, source_id, status="paused")
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return _to_read(ds)


@router.post("/{source_id}/resume", response_model=DataSourceRead)
async def resume_source(
    source_id: str,
    user_id: str = DEFAULT_USER_ID,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """恢复数据源。"""
    ds = await update_data_source(db, user_id, source_id, status="active")
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return _to_read(ds)


# ── 摄入状态迁移（Phase 1）──


@router.post("/ingestion/migrate")
async def migrate_ingestion_state() -> dict[str, Any]:
    """将旧 JSON 状态迁移到 DB。"""
    try:
        pipeline = get_pipeline()
    except AssertionError:
        raise HTTPException(status_code=503, detail="摄入管线未初始化")

    stats = await pipeline._store.migrate_from_json()
    return {"ok": True, "stats": stats}


@router.post("/ingestion/rollback")
async def rollback_ingestion_state() -> dict[str, Any]:
    """回滚：从备份 JSON 恢复，清空 DB 状态。"""
    try:
        pipeline = get_pipeline()
    except AssertionError:
        raise HTTPException(status_code=503, detail="摄入管线未初始化")

    ok = await pipeline._store.rollback()
    return {"ok": ok}


@router.get("/ingestion/verify")
async def verify_ingestion_migration() -> dict[str, Any]:
    """校验迁移一致性。"""
    try:
        pipeline = get_pipeline()
    except AssertionError:
        raise HTTPException(status_code=503, detail="摄入管线未初始化")

    result = await pipeline._store.verify_migration()
    return {"ok": True, **result}


# ── 内部辅助 ──


def _to_read(ds: Any) -> DataSourceRead:
    """将 ORM 模型转为响应 schema。"""
    return DataSourceRead(
        id=ds.id,
        user_id=ds.user_id,
        name=ds.name,
        type=ds.type,
        status=ds.status,
        config=ds.config_json or {},
        capabilities=ds.capabilities_json or [],
        last_sync_at=ds.last_sync_at,
        last_error=ds.last_error,
        created_at=ds.created_at,
        updated_at=ds.updated_at,
    )
