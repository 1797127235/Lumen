"""记忆管理相关路由（.md 画像 / 事件表 / 向量索引）。

这里的“记忆”有三层：
- **`.md` 画像文件**：面向用户展示的聚合结果（可读、可编辑/可重建）。
- **`GrowthEvent` 事件表**：用于追踪“新增/更新”的原子变更（可列表、可删除）。
- **Cognee 索引**：用于语义检索的外部索引/向量库（可能不可用，需降级）。
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text

from app.backend.db.base import get_async_session_maker
from app.backend.memory import get_memory
from app.backend.memory.cognee_admin import get_cognee_status
from app.backend.memory.projections.markdown import read_memory, sync_user_md_projection
from app.backend.models.growth_event import GrowthEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class MemoryContent(BaseModel):
    content: str


@router.get("/me", response_model=MemoryContent)
async def get_my_memory(user_id: str = Query("demo_user")) -> MemoryContent:
    """读取用户 `.md` 画像内容。

    如果文件为空，会尝试触发一次“从数据库事件 → 投影生成 .md”的同步，以便首次使用时也能拿到内容。
    """
    _validate_user_id(user_id)
    try:
        content = read_memory(user_id)
        if not content.strip():
            # `.md` 为空通常意味着尚未完成投影；这里做一次“自愈式”同步（失败则仍返回空）。
            projected = await sync_user_md_projection(user_id)
            if projected:
                content = read_memory(user_id)
        return MemoryContent(content=content)
    except Exception:
        logger.exception("Read memory failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="读取画像失败")


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
    """校验 `user_id` 的基本格式，避免把不受控字符串带进 DB/索引层。"""
    if not _USER_ID_PATTERN.match(user_id):
        raise HTTPException(
            status_code=400,
            detail="user_id 格式无效，只允许字母、数字、下划线和连字符，长度 1-64",
        )
    return user_id


@router.get("/stats", response_model=MemoryStats)
async def get_memory_stats(user_id: str = Query("demo_user")) -> MemoryStats:
    """返回记忆系统状态与事件数量。

    - **status**：当前 Cognee（索引/向量库）可用状态（例如 `ready`）。
    - **count**：数据库中该用户的 `GrowthEvent` 条目数。

    这里采用“尽量返回可用信息”的策略：数据库异常时返回 `count=0`，但仍返回 `status`。
    """
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
    """清空用户记忆（以事件表为准），并尽力同步其它层。

    行为：
    - 删除数据库中该用户所有 `GrowthEvent`
    - 触发 `.md` 投影同步（让画像回到“空/默认”状态）
    - 如果 Cognee 可用则尝试清理该用户索引（失败不影响 reset 主流程）
    """
    _validate_user_id(user_id)

    try:
        async with get_async_session_maker()() as db:
            result = await db.execute(select(func.count(GrowthEvent.id)).where(GrowthEvent.user_id == user_id))
            count = result.scalar() or 0
            await db.execute(delete(GrowthEvent).where(GrowthEvent.user_id == user_id))
            await db.commit()

        # reset 以后需要同步 `.md`，否则前端可能继续展示旧画像内容
        await sync_user_md_projection(user_id)

        index_cleared = False
        if get_cognee_status() == "ready":
            try:
                # 索引清理是“尽力而为”：外部服务抖动不应导致 reset 失败
                from app.backend.memory.stores.semantic import SemanticStore

                index_cleared = await SemanticStore().clear_index()
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
    """按时间倒序列出该用户的事件记忆（GrowthEvent）。

    备注：
    - `memory` 字段优先返回 `payload_json`（更完整）；若为空则回退到简短的拼接描述。
    - 出错时返回空列表（用于前端“可用即展示”的弱依赖场景）。
    """
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
    """重建用户记忆各层（通常包含 `.md` 与 Cognee 索引）。

    这是一个“运维/修复”入口：当投影或索引出现不一致时可用于全量重建。
    """
    _validate_user_id(user_id)

    try:
        result = await get_memory().rebuild(user_id)
        md_ok = result["md_success"]
        cognee_ok = result["cognee_success"]

        return {
            "message": "重建成功" if md_ok and cognee_ok else ".md 已重建，但 Cognee 重建失败",
            "user_id": user_id,
            **result,
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
    """在语义索引中检索记忆（优先走 Cognee/向量检索实现）。

    失败策略：返回空列表（避免检索服务不可用时拖垮主对话/页面渲染）。
    """
    _validate_user_id(user_id)

    try:
        memory = get_memory()
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


@router.post("/compensate")
async def compensate_cognee(user_id: str = Query("demo_user")) -> dict:
    """补偿扫描：重试 Cognee 投影失败的事件。"""
    _validate_user_id(user_id)
    try:
        fixed = await get_memory().compensate_cognee(user_id)
        return {"user_id": user_id, "compensated": fixed}
    except Exception as exc:
        logger.error("Cognee compensate failed: %s", exc)
        raise HTTPException(status_code=500, detail="补偿失败")


@router.delete("/{event_id}")
async def delete_memory(event_id: str, user_id: str = Query("demo_user")) -> dict:
    """删除单条事件记忆（GrowthEvent），并同步 `.md` 投影。

    重要约束：
    - 仅允许删除属于当前 `user_id` 的事件
    - 删除后需要重新投影 `.md`，避免画像仍包含已删除事件产生的内容
    """
    _validate_user_id(user_id)
    try:
        async with get_async_session_maker()() as db:
            event = await db.get(GrowthEvent, event_id)
            if event is None or event.user_id != user_id:
                raise HTTPException(status_code=404, detail="记忆不存在")

            from app.backend.memory.stores.relational import GrowthEventRepository

            repo = GrowthEventRepository(db)
            await db.execute(text("DELETE FROM growth_events WHERE id = :id"), {"id": event_id})
            await repo.rebuild_fts_index()
            await db.commit()

        # 删除事件后需要重新投影 `.md` 文件，确保用户画像与事件表一致。
        await sync_user_md_projection(user_id)

        logger.info("Memory deleted: id=%s, user_id=%s", event_id, user_id)
        return {"deleted": event_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Memory delete failed: %s", exc)
        raise HTTPException(status_code=500, detail="删除失败")
