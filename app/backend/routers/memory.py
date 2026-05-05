"""记忆管理路由 — Cognee 状态查询与重置"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.backend.agent.cognee_client import get_cognee_status
from app.backend.services import cognee_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

# user_id 验证：只允许字母、数字、下划线、连字符，长度 1-64
_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class MemoryStats(BaseModel):
    status: str  # ready / not_installed / error / not_initialized
    count: int


class MemoryResetResponse(BaseModel):
    deleted: int


class MemoryItem(BaseModel):
    id: str
    memory: str
    created_at: str | None = None
    categories: list[str] = []


def _validate_user_id(user_id: str) -> str:
    """验证 user_id 格式"""
    if not _USER_ID_PATTERN.match(user_id):
        raise HTTPException(status_code=400, detail="user_id 格式无效，只允许字母、数字、下划线、连字符，长度 1-64")
    return user_id


@router.get("/stats", response_model=MemoryStats)
async def get_memory_stats(user_id: str = Query("demo_user")) -> MemoryStats:
    """查询当前记忆状态和条数（基于 SQLite growth_events）"""
    _validate_user_id(user_id)
    status = get_cognee_status()

    try:
        # 从 SQLite 查询 growth_events 数量（不依赖 Cognee 状态）
        from sqlalchemy import func, select

        from app.backend.db.session import get_db
        from app.backend.models.growth_event import GrowthEvent

        # 使用 FastAPI DI 获取 session
        async for db in get_db():
            result = await db.execute(select(func.count(GrowthEvent.id)).where(GrowthEvent.user_id == user_id))
            count = result.scalar() or 0
            return MemoryStats(status=status, count=count)
    except Exception as e:
        logger.error("记忆条数查询失败: %s", e)
        return MemoryStats(status=status, count=0)


@router.post("/reset", response_model=MemoryResetResponse)
async def reset_memory(user_id: str = Query("demo_user")) -> MemoryResetResponse:
    """清空指定用户的长期记忆事件（基于 SQLite growth_events）"""
    _validate_user_id(user_id)

    try:
        # 删除 SQLite 中的 growth_events（不依赖 Cognee 状态）
        from sqlalchemy import delete, func, select

        from app.backend.db.session import get_db
        from app.backend.models.growth_event import GrowthEvent

        # 使用 FastAPI DI 获取 session，确保事务一致性
        async for db in get_db():
            # 先查条数
            result = await db.execute(select(func.count(GrowthEvent.id)).where(GrowthEvent.user_id == user_id))
            count = result.scalar() or 0

            # 删除事件（在同一事务中）
            await db.execute(delete(GrowthEvent).where(GrowthEvent.user_id == user_id))
            # get_db 会自动 commit

        # 尝试清空 Cognee 索引（可选，不影响主流程）
        index_cleared = False
        if get_cognee_status() == "ready":
            try:
                index_cleared = await cognee_service.clear_user_index(user_id)
            except Exception as cognee_exc:
                logger.warning("Cognee clear_user_index failed after SQLite reset: %s", cognee_exc)

        logger.info("记忆已重置: user_id=%s, deleted=%d, index_cleared=%s", user_id, count, index_cleared)
        return MemoryResetResponse(deleted=count)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("记忆重置失败: %s", e)
        raise HTTPException(status_code=500, detail="清空失败，请查看日志")


@router.get("/list", response_model=list[MemoryItem])
async def list_memories(user_id: str = Query("demo_user")) -> list[MemoryItem]:
    """返回用户所有记忆条目，按 created_at 倒序（基于 SQLite growth_events）"""
    _validate_user_id(user_id)

    try:
        from sqlalchemy import select

        from app.backend.db.session import get_db
        from app.backend.models.growth_event import GrowthEvent

        # 使用 FastAPI DI 获取 session（不依赖 Cognee 状态）
        async for db in get_db():
            result = await db.execute(
                select(GrowthEvent).where(GrowthEvent.user_id == user_id).order_by(GrowthEvent.created_at.desc())
            )
            events = result.scalars().all()

            memories: list[MemoryItem] = []
            for event in events:
                # 确保 id 是字符串
                memories.append(
                    MemoryItem(
                        id=str(event.id),
                        memory=event.payload_json or f"{event.event_type}: {event.entity_type or 'unknown'}",
                        created_at=event.created_at.isoformat() if event.created_at else None,
                        categories=[event.event_type] if event.event_type else [],
                    )
                )

            return memories
    except Exception as e:
        logger.error("记忆列表查询失败: %s", e)
        return []


@router.post("/rebuild")
async def rebuild_memory(user_id: str = Query("demo_user")) -> dict:
    """从 SQLite 重建 Cognee 索引"""
    _validate_user_id(user_id)
    status = get_cognee_status()
    if status != "ready":
        raise HTTPException(status_code=503, detail="记忆服务未就绪")

    try:
        from app.backend.services.cognee_projector import project_all_events

        success = await project_all_events(user_id)
        if success:
            return {"message": "重建成功", "user_id": user_id}
        else:
            raise HTTPException(status_code=500, detail="重建失败")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("记忆重建失败: %s", e)
        raise HTTPException(status_code=500, detail="重建失败，请查看日志")
