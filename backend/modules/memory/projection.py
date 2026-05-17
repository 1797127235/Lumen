"""记忆投影与管理层 — .md 同步、重建、删除、重置。"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from sqlalchemy import distinct, select

from backend.core.db import get_async_session_maker
from backend.core.logging import get_logger
from backend.modules.memory.markdown import sync_user_md_projection
from backend.modules.memory.models import GrowthEvent
from backend.modules.memory.snapshot import invalidate_cache

logger = get_logger(__name__)


_DEFAULT_COMPENSATION_INTERVAL = 300  # 5 分钟


def cancel_background_tasks() -> None:
    """FastAPI shutdown 钩子。取消 ProjectionManager 的所有后台任务。"""
    ProjectionManager.cancel_all_tasks()


class ProjectionManager:
    """投影同步与记忆管理职责 — 被 LumenMemory 组合。"""

    _background_tasks: ClassVar[set[asyncio.Task]] = set()  # type: ignore[type-arg]

    @classmethod
    def cancel_all_tasks(cls) -> None:
        for task in cls._background_tasks:
            if not task.done():
                task.cancel()
        cls._background_tasks.clear()

    async def _update_understanding(self, user_id: str) -> None:
        """后台异步：更新 AI 综合画像。

        写入 about_you.md 后使快照缓存失效，确保下一次 build_snapshot 读取到最新画像。
        内部有防抖——同一用户已有任务或最近更新过时直接跳过，不创建新 task。
        """
        try:
            from backend.modules.memory.understanding import update_ai_understanding

            await update_ai_understanding(user_id)
            await invalidate_cache(user_id)
        except asyncio.CancelledError:
            logger.debug("AI understanding cancelled on shutdown", user_id=user_id)
        except Exception as exc:
            logger.warning("AI understanding update skipped", user_id=user_id, error=str(exc))

    async def sync_projections(
        self,
        user_id: str,
        event_ids: list[str] | None = None,
        *,
        db=None,
    ) -> None:
        """同步 .md 投影 + Provider 语义索引 + 快照缓存。

        db 传入:  sync_user_md_projection 使用指定 session（不 commit）。
        db=None:  sync_user_md_projection 自开 session + commit。

        Provider 同步始终独立 session（外部 IO，不参与 DB 事务）。
        """
        # Phase 1: DB — .md 投影（共享 session 时可与上游写入原子提交）
        await sync_user_md_projection(user_id, db=db)

        # Phase 2: 缓存 + 后台 AI 画像
        await invalidate_cache(user_id)
        task = asyncio.create_task(self._update_understanding(user_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        # Phase 3: Provider 语义索引（独立 session，外部 IO，最终一致性）
        try:
            await self._sync_narrative_to_provider(user_id, event_ids)
        except Exception:
            logger.exception("Provider sync failed", user_id=user_id)

    async def _sync_narrative_to_provider(self, user_id: str, event_ids: list[str] | None = None) -> None:
        """将未索引的 Narrative 事件同步到 DocumentIndexProvider（语义搜索）。

        依赖 projected_provider_at 字段跟踪同步状态：
        - NULL → 未同步，需要处理
        - 非 NULL → 已同步，跳过

        在 commit 后的独立 session 中执行，不影响主事务。
        safe: Provider 不可用（NullProvider / 未安装）时静默跳过。
        """
        from backend.modules.data_sources.ingestion import get_document_index_provider
        from backend.modules.data_sources.ingestion.providers.null import NullProvider

        provider = get_document_index_provider()
        if provider is None or isinstance(provider, NullProvider):
            return

        from backend.modules.memory.classifier import NARRATIVE_EVENT_TYPES
        from backend.modules.memory.relational_store import GrowthEventRepository

        async with get_async_session_maker()() as db:
            repo = GrowthEventRepository(db)

            if event_ids:
                events = await repo.get_batch(event_ids, user_id)
            else:
                events = await repo.get_needing_projection(user_id, projection_field="projected_provider_at")

            narrative_events = [e for e in events if e.event_type in NARRATIVE_EVENT_TYPES]
            if not narrative_events:
                return

            import json
            from contextlib import suppress
            from datetime import UTC, datetime

            now = datetime.now(UTC)
            for event in narrative_events:
                payload = {}
                if event.payload_json:
                    with suppress(json.JSONDecodeError):
                        payload = json.loads(event.payload_json)

                content = (
                    payload.get("content")
                    or payload.get("description")
                    or payload.get("decision")
                    or payload.get("memory_md")
                    or (json.dumps(payload, ensure_ascii=False) if payload else "")
                    or f"{event.event_type}: {event.entity_type or ''}"
                )

                try:
                    await provider.sync_document(
                        content=content[:50000],
                        doc_id=f"narrative:{event.id}",
                        metadata={
                            "event_type": event.event_type,
                            "entity_type": event.entity_type,
                            "entity_id": event.entity_id,
                            "user_id": user_id,
                            "source": event.source,
                            "created_at": event.created_at.isoformat() if event.created_at else None,
                        },
                    )
                    event.projected_provider_at = now
                except Exception:
                    logger.exception(
                        "Provider sync failed for event",
                        event_id=event.id,
                        event_type=event.event_type,
                    )
                    # 不更新时间戳，下次 get_needing_projection 会继续重试

            await db.commit()

    @classmethod
    def start_provider_compensation_loop(cls, interval: int = _DEFAULT_COMPENSATION_INTERVAL) -> asyncio.Task:  # type: ignore[type-arg]
        """启动后台语义索引补偿循环。

        定时扫描所有 `projected_provider_at IS NULL` 的 Narrative 事件，
        补偿同步到 DocumentIndexProvider。不依赖新对话触发。
        """
        task = asyncio.create_task(_provider_compensation_loop(interval), name="provider-compensation")
        cls._background_tasks.add(task)
        task.add_done_callback(cls._background_tasks.discard)
        return task

    async def force_md_rebuild(self, user_id: str) -> bool:
        """强制全量重建 .md，不检查 dirty 标记。删除事件后调用。"""
        from backend.modules.memory.markdown import project_user_to_md

        async with get_async_session_maker()() as db:
            success = await project_user_to_md(db, user_id)
            if success:
                await db.commit()
            else:
                await db.rollback()
        await invalidate_cache(user_id)
        # 重建 .md 后也更新 AI 综合画像
        task = asyncio.create_task(self._update_understanding(user_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
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

        await invalidate_cache(user_id)
        # 重建 .md 后也更新 AI 综合画像
        task = asyncio.create_task(self._update_understanding(user_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        # FTS5 触发器自动维护，无需手动索引
        return {"md_success": md_success}

    async def delete_event(self, user_id: str, event_id: str) -> tuple[bool, str | None]:
        """删除单条事件。

        先禁用 FTS 触发器再删除，避免触发器因数据不一致报错，
        然后重建 FTS 索引确保同步。
        """
        from backend.modules.memory.relational_store import GrowthEventRepository

        async with get_async_session_maker()() as db:
            event = await db.get(GrowthEvent, event_id)

            if event is None:
                return False, "记忆不存在"
            if event.user_id != user_id:
                return False, "无权删除该记忆"

            try:
                store = GrowthEventRepository(db)
                await store.drop_fts_triggers()
                await db.delete(event)
                await db.commit()
                await store.rebuild_fts_index()
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
        await invalidate_cache(user_id)
        return count

    async def reset(self, user_id: str) -> dict:
        """清空用户全部记忆（事件表 + .md + Provider 索引）。

        Returns:
            {"deleted": int, "index_cleared": bool}
        """
        deleted = await self.delete_all_events(user_id)

        index_cleared = False
        try:
            from backend.modules.data_sources.ingestion import get_document_index_provider

            provider = get_document_index_provider()
            if provider is not None:
                index_cleared = await provider.clear()
        except Exception as exc:
            logger.warning("Provider clear failed after reset: %s", exc)

        logger.info("Memory reset: user_id=%s, deleted=%d, index_cleared=%s", user_id, deleted, index_cleared)
        return {"deleted": deleted, "index_cleared": index_cleared}


async def _provider_compensation_loop(interval: int) -> None:
    """后台语义索引补偿：定时扫描未同步事件并补同步到 Provider。"""
    from backend.modules.data_sources.ingestion import get_document_index_provider
    from backend.modules.data_sources.ingestion.providers.null import NullProvider

    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.debug("Provider compensation loop cancelled")
            raise

        provider = get_document_index_provider()
        if provider is None or isinstance(provider, NullProvider):
            continue

        async with get_async_session_maker()() as db:
            result = await db.execute(
                select(distinct(GrowthEvent.user_id)).where(GrowthEvent.projected_provider_at.is_(None))
            )
            user_ids = [row[0] for row in result.all()]

        if not user_ids:
            continue

        logger.debug("Provider compensation scanning", users=len(user_ids))

        for user_id in user_ids:
            try:
                manager = ProjectionManager()
                await manager._sync_narrative_to_provider(user_id)
            except Exception:
                logger.exception("Provider compensation failed", user_id=user_id)
