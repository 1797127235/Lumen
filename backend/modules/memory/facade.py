"""记忆层门面 — LumenMemory 统一入口。

通过多重继承组合三个职责模块。LumenMemory 显式编排 write → commit → projection 流程，
不存在 MRO 隐藏契约。

双管线架构：
- Profile 事件 → .md 投影 + L0 固定注入（不进搜索索引）
- Narrative 事件 → FTS5 索引 + L2 按需召回（Provider 语义搜索）
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_async_session_maker
from backend.modules.memory.models import GrowthEvent
from backend.modules.memory.projection import ProjectionManager, cancel_background_tasks
from backend.modules.memory.searcher import MemorySearcher
from backend.modules.memory.writer import EventSpec, MemoryWriter


class LumenMemory(MemoryWriter, MemorySearcher, ProjectionManager):
    """记忆层统一门面 — 单例，无状态。

    继承顺序（有意为之）：
    - MemoryWriter:   _write_events / remember(db=必需) / remember_batch(db=必需)
    - MemorySearcher: recall / build_context / list_events / count_events
    - ProjectionManager: sync_projections / rebuild / delete_event / reset

    LumenMemory 覆盖 remember / remember_batch 以提供 db=None 便利路径：
    自开 session → 写入(flush) → 投影(flush, 同 session) → commit。
    事件写入和 .md 投影在同一事务内，要么全成功要么全回滚。
    """

    # ── 写入便利方法（db=None → 自管 session + commit + 投影）─────────

    async def remember(
        self,
        user_id: str,
        event_type: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        payload: dict | None = None,
        source: str = "system",
        *,
        db: AsyncSession | None = None,
    ) -> GrowthEvent | None:
        """写入一条记忆事件。

        db 传入:  仅 flush，调用方负责 commit + 投影。
        db=None:  自开 session，写入 + 投影 + commit 在同一事务。
        """
        if db is not None:
            return await MemoryWriter.remember(
                self,
                user_id,
                event_type,
                entity_type,
                entity_id,
                payload,
                source,
                db=db,
            )

        async with get_async_session_maker()() as session:
            event = await MemoryWriter.remember(
                self,
                user_id,
                event_type,
                entity_type,
                entity_id,
                payload,
                source,
                db=session,
            )
            if event:
                # 投影与写入在同一 session，commit 之前完成 → 事务原子性
                await ProjectionManager.sync_projections(
                    self,
                    user_id,
                    [str(event.id)],
                    db=session,
                )
                await session.commit()
            return event

    async def remember_batch(
        self,
        user_id: str,
        events: list[EventSpec],
        *,
        db: AsyncSession | None = None,
    ) -> list:
        """批量写入。

        db 传入:  仅 flush。
        db=None:  自开 session，写入 + 投影 + commit 在同一事务。
        """
        if db is not None:
            return await MemoryWriter.remember_batch(self, user_id, events, db=db)

        async with get_async_session_maker()() as session:
            created = await MemoryWriter.remember_batch(self, user_id, events, db=session)
            if created:
                await ProjectionManager.sync_projections(
                    self,
                    user_id,
                    [str(e.id) for e in created],
                    db=session,
                )
                await session.commit()
            return created

    async def update_event(
        self,
        user_id: str,
        event_id: str,
        payload: dict | None = None,
    ) -> tuple[bool, str | None]:
        """更新记忆事件内容，更新后触发投影重建。"""
        from backend.modules.memory.relational_store import GrowthEventRepository

        async with get_async_session_maker()() as session:
            repo = GrowthEventRepository(session)
            event = await repo.update_event(event_id, user_id, payload=payload)
            if event is None:
                return False, "记忆不存在或无权修改"
            # 标记为已编辑
            event.confirmation_status = "modified"
            event.reviewed_at = datetime.now(UTC)
            await session.commit()

        await self.force_md_rebuild(user_id)
        return True, None

    async def review_event(
        self,
        user_id: str,
        event_id: str,
        status: str,
    ) -> tuple[bool, str | None]:
        """审核记忆事件：confirmed / rejected。"""
        if status not in ("confirmed", "rejected"):
            return False, "status 必须是 confirmed 或 rejected"

        from backend.modules.memory.relational_store import GrowthEventRepository

        async with get_async_session_maker()() as session:
            repo = GrowthEventRepository(session)
            event = await repo.review_event(event_id, user_id, confirmation_status=status)
            if event is None:
                return False, "记忆不存在或无权修改"
            await session.commit()

        await self.force_md_rebuild(user_id)
        return True, None


_memory: LumenMemory | None = None
_init_lock = threading.Lock()


def get_memory() -> LumenMemory:
    global _memory
    if _memory is None:
        with _init_lock:
            if _memory is None:
                _memory = LumenMemory()
    return _memory


__all__ = ["EventSpec", "LumenMemory", "cancel_background_tasks", "get_memory"]
