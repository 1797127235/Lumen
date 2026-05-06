"""Memory management routes."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.backend.agent.cognee_client import get_cognee_status
from app.backend.db.base import get_async_session_maker
from app.backend.models.growth_event import GrowthEvent
from app.backend.services import cognee_service
from app.backend.services.cognee_projector import project_all_events
from app.backend.services.md_projector import project_user_to_md, sync_user_md_projection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class MemoryStats(BaseModel):
    status: str
    count: int


class MemoryResetResponse(BaseModel):
    deleted: int
    index_cleared: bool = False


class MemoryItem(BaseModel):
    id: str
    memory: str
    created_at: str | None = None
    categories: list[str] = Field(default_factory=list)


def _validate_user_id(user_id: str) -> str:
    if not _USER_ID_PATTERN.match(user_id):
        raise HTTPException(
            status_code=400,
            detail="user_id 格式无效，只允许字母、数字、下划线和连字符，长度 1-64",
        )
    return user_id


@router.get("/stats", response_model=MemoryStats)
async def get_memory_stats(user_id: str = Query("demo_user")) -> MemoryStats:
    _validate_user_id(user_id)
    status = get_cognee_status()

    try:
        async with get_async_session_maker()() as db:
            result = await db.execute(select(func.count(GrowthEvent.id)).where(GrowthEvent.user_id == user_id))
            count = result.scalar() or 0
        return MemoryStats(status=status, count=count)
    except Exception as exc:
        logger.error("Memory stats failed: %s", exc)
        return MemoryStats(status=status, count=0)


@router.post("/reset", response_model=MemoryResetResponse)
async def reset_memory(user_id: str = Query("demo_user")) -> MemoryResetResponse:
    _validate_user_id(user_id)

    try:
        async with get_async_session_maker()() as db:
            result = await db.execute(select(func.count(GrowthEvent.id)).where(GrowthEvent.user_id == user_id))
            count = result.scalar() or 0
            await db.execute(delete(GrowthEvent).where(GrowthEvent.user_id == user_id))
            await db.commit()

        await sync_user_md_projection(user_id)

        index_cleared = False
        if get_cognee_status() == "ready":
            try:
                index_cleared = await cognee_service.clear_user_index(user_id)
            except Exception as exc:
                logger.warning("Cognee clear failed after reset: %s", exc)

        logger.info("Memory reset: user_id=%s, deleted=%d, index_cleared=%s", user_id, count, index_cleared)
        return MemoryResetResponse(deleted=count, index_cleared=index_cleared)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Memory reset failed: %s", exc)
        raise HTTPException(status_code=500, detail="清空失败，请查看日志")


@router.get("/list", response_model=list[MemoryItem])
async def list_memories(user_id: str = Query("demo_user")) -> list[MemoryItem]:
    _validate_user_id(user_id)
    try:
        async with get_async_session_maker()() as db:
            result = await db.execute(
                select(GrowthEvent).where(GrowthEvent.user_id == user_id).order_by(GrowthEvent.created_at.desc())
            )
            events = result.scalars().all()
        return [
            MemoryItem(
                id=str(event.id),
                memory=event.payload_json or f"{event.event_type}: {event.entity_type or 'unknown'}",
                created_at=event.created_at.isoformat() if event.created_at else None,
                categories=[event.event_type] if event.event_type else [],
            )
            for event in events
        ]
    except Exception as exc:
        logger.error("Memory list failed: %s", exc)
        return []


@router.post("/rebuild")
async def rebuild_memory(user_id: str = Query("demo_user")) -> dict:
    _validate_user_id(user_id)
    status = get_cognee_status()

    try:
        async with get_async_session_maker()() as db:
            md_success = await project_user_to_md(db, user_id)
            if md_success:
                await db.commit()
            else:
                await db.rollback()

        cognee_success = status != "ready"
        index_cleared = False
        if status == "ready":
            index_cleared = await cognee_service.clear_user_index(user_id)
            cognee_success = index_cleared and await project_all_events(user_id)

        return {
            "message": "重建成功" if md_success and cognee_success else ".md 已重建，但 Cognee 重建失败",
            "user_id": user_id,
            "md_success": md_success,
            "cognee_success": cognee_success,
            "index_cleared": index_cleared,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Memory rebuild failed: %s", exc)
        raise HTTPException(status_code=500, detail="重建失败，请查看日志")


@router.get("/search")
async def search_memories(
    user_id: str = Query("demo_user"),
    query: str = Query(...),
    limit: int = Query(10),
) -> list[MemoryItem]:
    _validate_user_id(user_id)

    try:
        from app.backend.services.careeros_memory import get_memory as _get_mem

        memory = _get_mem()
        items = await memory.recall(user_id, query, limit=limit)
        return [
            MemoryItem(
                id=item.id,
                memory=item.content,
                created_at=item.created_at,
                categories=item.categories,
            )
            for item in items
        ]
    except Exception as exc:
        logger.error("Memory search failed: %s", exc)
        return []
