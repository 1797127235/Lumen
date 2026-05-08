"""记忆管理路由 — 纯 HTTP 协议层。

业务逻辑已迁移到 services/memory_service.py。
路由只负责：参数校验、调用 service、HTTP 响应转换。
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.backend.services import memory_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


# ── 响应模型 ──


class MemoryContent(BaseModel):
    content: str


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


# ── 工具函数 ──


def _validate_user_id(user_id: str) -> str:
    if not _USER_ID_PATTERN.match(user_id):
        raise HTTPException(
            status_code=400,
            detail="user_id 格式无效，只允许字母、数字、下划线和连字符，长度 1-64",
        )
    return user_id


# ── 路由 ──


@router.get("/me", response_model=MemoryContent)
async def get_my_memory(user_id: str = Query("demo_user")) -> MemoryContent:
    """读取用户 .md 画像内容。"""
    _validate_user_id(user_id)
    try:
        content = await memory_service.get_memory_content(user_id)
        return MemoryContent(content=content)
    except Exception:
        logger.exception("Read memory failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="读取画像失败") from None


@router.get("/stats", response_model=MemoryStats)
async def get_memory_stats(user_id: str = Query("demo_user")) -> MemoryStats:
    """记忆状态与事件数量。"""
    _validate_user_id(user_id)
    result = await memory_service.get_memory_stats(user_id)
    return MemoryStats(**result)


@router.post("/reset", response_model=MemoryResetResponse)
async def reset_memory(user_id: str = Query("demo_user")) -> MemoryResetResponse:
    """清空用户记忆（事件表 + .md + Cognee 索引）。"""
    _validate_user_id(user_id)
    try:
        result = await memory_service.reset_memory(user_id)
        return MemoryResetResponse(**result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Memory reset failed: %s", exc)
        raise HTTPException(status_code=500, detail="清空失败，请查看日志")


@router.get("/list", response_model=list[MemoryItem])
async def list_memories(user_id: str = Query("demo_user")) -> list[MemoryItem]:
    """按时间倒序列出事件记忆。"""
    _validate_user_id(user_id)
    items = await memory_service.list_memories(user_id)
    return [MemoryItem(**item) for item in items]


@router.post("/rebuild")
async def rebuild_memory(user_id: str = Query("demo_user")) -> dict:
    """全量重建 .md + Cognee 索引。"""
    _validate_user_id(user_id)
    try:
        result = await memory_service.rebuild_memory(user_id)
        md_ok = result.get("md_success", False)
        cognee_ok = result.get("cognee_success")

        msg = "重建成功"
        if not md_ok:
            msg = ".md 重建失败"
        elif cognee_ok is False:
            msg = ".md 已重建，但 Cognee 重建失败"

        return {"message": msg, "user_id": user_id, **result}
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
    """语义 + FTS5 + .md 多源搜索记忆。"""
    _validate_user_id(user_id)
    items = await memory_service.search_memories(user_id, query, limit=limit)
    return [MemoryItem(**item) for item in items]


@router.post("/compensate")
async def compensate_cognee(user_id: str = Query("demo_user")) -> dict:
    """补偿扫描：重试 Cognee 投影失败的事件。"""
    _validate_user_id(user_id)
    try:
        fixed = await memory_service.compensate_cognee(user_id)
        return {"user_id": user_id, "compensated": fixed}
    except Exception as exc:
        logger.error("Cognee compensate failed: %s", exc)
        raise HTTPException(status_code=500, detail="补偿失败")


@router.delete("/{event_id}")
async def delete_memory(event_id: str, user_id: str = Query("demo_user")) -> dict:
    """删除单条事件记忆并同步投影。"""
    _validate_user_id(user_id)
    try:
        success, error = await memory_service.delete_memory(user_id, event_id)
        if not success:
            status_code = 403 if error and "无权" in error else 404
            raise HTTPException(status_code=status_code, detail=error or "删除失败")
        return {"deleted": event_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Memory delete failed: %s", exc)
        raise HTTPException(status_code=500, detail="删除失败")
