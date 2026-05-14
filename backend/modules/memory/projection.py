"""记忆投影与管理层 — .md 同步、重建、删除、重置。"""

from __future__ import annotations

import asyncio

from backend.core.db import get_async_session_maker
from backend.core.logging import get_logger
from backend.modules.memory.markdown import sync_user_md_projection
from backend.modules.memory.models import GrowthEvent
from backend.modules.memory.snapshot import invalidate_cache

logger = get_logger(__name__)

# 后台任务生命周期
_background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]


def cancel_background_tasks() -> None:
    """FastAPI shutdown 钩子。"""
    for task in _background_tasks:
        if not task.done():
            task.cancel()
    _background_tasks.clear()


class ProjectionManager:
    """投影同步与记忆管理职责 — 被 LumenMemory 组合。"""

    async def _update_understanding(self, user_id: str) -> None:
        """后台异步：更新 AI 综合画像。

        写入 about_you.md 后使快照缓存失效，
        确保下一次 build_snapshot 读取到最新画像。
        """
        try:
            from backend.modules.memory.understanding import update_ai_understanding

            await update_ai_understanding(user_id)
            # about_you.md 已落盘，令下次 build_snapshot 重新读取
            invalidate_cache(user_id)
        except Exception as exc:
            logger.warning("AI understanding update skipped", user_id=user_id, error=str(exc))

    async def sync_projections(self, user_id: str, event_ids: list[str] | None = None) -> None:
        """同步 .md 投影与快照缓存，按需触发 AI 画像更新。

        调用方负责事务（自开 session 或外部 db 路径均可）。
        Profile 事件 → .md 投影 + AI 综合画像更新
        Narrative 事件 → FTS5 触发器自动增量索引（无需手动同步）
        """
        await sync_user_md_projection(user_id)
        invalidate_cache(user_id)
        if event_ids:
            task = asyncio.create_task(self._update_understanding(user_id))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

    # 别名：保持向后兼容，内部自开 session 路径与外部 db 路径行为一致
    flush_projections = sync_projections

    async def force_md_rebuild(self, user_id: str) -> bool:
        """强制全量重建 .md，不检查 dirty 标记。删除事件后调用。"""
        from backend.modules.memory.markdown import project_user_to_md

        async with get_async_session_maker()() as db:
            success = await project_user_to_md(db, user_id)
            if success:
                await db.commit()
            else:
                await db.rollback()
        invalidate_cache(user_id)
        return success

    async def rebuild(self, user_id: str) -> dict:
        """全量重建 .md 投影 + FTS5 索引。"""
        from backend.modules.memory.markdown import project_user_to_md

        async with get_async_session_maker()() as db:
            md_success = await project_user_to_md(db, user_id)
            if md_success:
                await db.commit()
            else:
                await db.rollback()

        invalidate_cache(user_id)

        # FTS5 触发器自动维护，无需手动索引
        return {"md_success": md_success}

    async def delete_event(self, user_id: str, event_id: str) -> tuple[bool, str | None]:
        """删除单条事件，FTS5 触发器自动增量更新索引。

        不再全量重建 FTS — AFTER DELETE 触发器已处理增量删除。
        仅当 FTS 损坏时才需要 rebuild_fts_index()。
        """
        async with get_async_session_maker()() as db:
            event = await db.get(GrowthEvent, event_id)

            if event is None:
                return False, "记忆不存在"
            if event.user_id != user_id:
                return False, "无权删除该记忆"

            try:
                await db.delete(event)
                await db.commit()
            except Exception:
                await db.rollback()
                return False, "数据库操作失败"

        await self.force_md_rebuild(user_id)
        return True, None

    async def delete_all_events(self, user_id: str) -> int:
        """删除用户全部事件（含 FTS 重建 + .md 同步）。返回删除数。"""
        from sqlalchemy import delete, select
        from sqlalchemy import func as _func

        from backend.modules.memory.relational_store import GrowthEventRepository

        async with get_async_session_maker()() as db:
            result = await db.execute(select(_func.count(GrowthEvent.id)).where(GrowthEvent.user_id == user_id))
            count = result.scalar() or 0

            # 先禁用触发器，避免 FTS 表缺失导致删除失败
            store = GrowthEventRepository(db)
            await store.drop_fts_triggers()

            await db.execute(delete(GrowthEvent).where(GrowthEvent.user_id == user_id))
            await db.commit()

            # 重建 FTS（表为空，触发器也重建）
            await store.rebuild_fts_index()

        # 同步 .md 到空状态
        await sync_user_md_projection(user_id)
        invalidate_cache(user_id)
        return count

    async def reset(self, user_id: str) -> dict:
        """清空用户全部记忆（事件表 + .md + Cognee 索引）。

        Returns:
            {"deleted": int, "index_cleared": bool}
        """
        deleted = await self.delete_all_events(user_id)

        index_cleared = False
        try:
            from backend.modules.data_sources.ingestion.document_index_provider import get_document_index_provider

            provider = get_document_index_provider()
            index_cleared = await provider.clear()
        except Exception as exc:
            logger.warning("Provider clear failed after reset: %s", exc)

        logger.info("Memory reset: user_id=%s, deleted=%d, index_cleared=%s", user_id, deleted, index_cleared)
        return {"deleted": deleted, "index_cleared": index_cleared}

    @staticmethod
    def cognee_status() -> str:
        """返回 Cognee 索引状态：'ready' | 'not_initialized' | 'error'。"""
        from backend.modules.memory.cognify_loop import get_cognee_status

        return get_cognee_status()
