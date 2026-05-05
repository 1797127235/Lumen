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
    """清空指定用户的长期记忆（SQLite + .md + Cognee）"""
    _validate_user_id(user_id)

    try:
        # 1. 删除 SQLite 中的 growth_events
        from sqlalchemy import delete, func, select

        from app.backend.db.session import get_db
        from app.backend.models.growth_event import GrowthEvent

        async for db in get_db():
            result = await db.execute(select(func.count(GrowthEvent.id)).where(GrowthEvent.user_id == user_id))
            count = result.scalar() or 0
            await db.execute(delete(GrowthEvent).where(GrowthEvent.user_id == user_id))

        # 2. 清空 .md 文件（重置为空模板）
        from app.backend.services.memory_service import (
            _default_entity_template,
            _default_memory_template,
            ensure_memory_dirs,
            write_entity,
            write_memory,
        )

        ensure_memory_dirs()
        write_memory(_default_memory_template())
        for entity_type in ["skills", "experiences", "preferences", "goals", "decisions", "relationships", "status"]:
            write_entity(entity_type, _default_entity_template(entity_type))

        # 3. 尝试清空 Cognee 索引
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
    """从 SQLite 重建 .md 和 Cognee 索引

    流程：
    1. 清空 .md 文件（重置为空模板）
    2. 从 SQLite 重建 .md 文件
    3. 清空 Cognee 索引
    4. 从 SQLite 重建 Cognee 索引
    """
    _validate_user_id(user_id)
    status = get_cognee_status()
    if status != "ready":
        raise HTTPException(status_code=503, detail="记忆服务未就绪")

    try:
        # 1. 从 SQLite 重建 .md 文件
        from app.backend.db.base import get_async_session_maker
        from app.backend.services.md_projector import project_user_to_md

        async with get_async_session_maker()() as db:
            md_success = await project_user_to_md(db, user_id)

        # 2. 清空 Cognee 索引
        cognee_success = False
        if status == "ready":
            try:
                await cognee_service.clear_user_index(user_id)
            except Exception as e:
                logger.warning("清空 Cognee 索引失败: %s", e)

        # 3. 从 SQLite 重建 Cognee 索引
        from app.backend.services.cognee_projector import project_all_events

        cognee_success = await project_all_events(user_id)

        if md_success and cognee_success:
            return {"message": "重建成功", "user_id": user_id}
        elif md_success:
            return {"message": ".md 重建成功，Cognee 重建失败", "user_id": user_id}
        else:
            raise HTTPException(status_code=500, detail="重建失败")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("记忆重建失败: %s", e)
        raise HTTPException(status_code=500, detail="重建失败，请查看日志")


@router.get("/search")
async def search_memories(
    user_id: str = Query("demo_user"),
    query: str = Query(...),
    limit: int = Query(10),
) -> list[MemoryItem]:
    """搜索记忆（同时查 .md 和 events）

    Args:
        user_id: 用户 ID
        query: 搜索关键词
        limit: 返回数量限制

    Returns:
        匹配的记忆列表
    """
    _validate_user_id(user_id)

    try:
        from sqlalchemy import or_, select

        from app.backend.db.session import get_db
        from app.backend.models.growth_event import GrowthEvent

        results: list[MemoryItem] = []

        # 1. 从 SQLite events 搜索
        async for db in get_db():
            result = await db.execute(
                select(GrowthEvent)
                .where(
                    GrowthEvent.user_id == user_id,
                    or_(
                        GrowthEvent.payload_json.contains(query),
                        GrowthEvent.event_type.contains(query),
                        GrowthEvent.entity_type.contains(query),
                    ),
                )
                .order_by(GrowthEvent.created_at.desc())
                .limit(limit)
            )
            events = result.scalars().all()

            for event in events:
                results.append(
                    MemoryItem(
                        id=str(event.id),
                        memory=event.payload_json or f"{event.event_type}: {event.entity_type or 'unknown'}",
                        created_at=event.created_at.isoformat() if event.created_at else None,
                        categories=[event.event_type] if event.event_type else [],
                    )
                )

        # 2. 从 .md 文件搜索
        from app.backend.services.memory_service import search_memory

        md_results = search_memory(query)
        for md_result in md_results:
            results.append(
                MemoryItem(
                    id=f"md:{md_result['file']}",
                    memory=md_result["content"],
                    created_at=None,
                    categories=[md_result["section"]],
                )
            )

        # 去重并限制数量
        seen = set()
        unique_results = []
        for item in results:
            if item.id not in seen:
                seen.add(item.id)
                unique_results.append(item)
                if len(unique_results) >= limit:
                    break

        return unique_results
    except Exception as e:
        logger.error("记忆搜索失败: %s", e)
        return []
