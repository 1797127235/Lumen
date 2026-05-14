"""记忆写入层 — 事件写入、单条/批量记录。"""

from __future__ import annotations

from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import get_async_session_maker
from backend.core.logging import get_logger
from backend.modules.memory.models import GrowthEvent
from backend.modules.memory.relational_store import GrowthEventRepository

logger = get_logger(__name__)


class EventSpec(TypedDict, total=False):
    event_type: str
    entity_type: str | None
    entity_id: str | None
    payload: dict | None
    source: str


class MemoryWriter:
    """事件写入职责 — 被 LumenMemory 组合。"""

    async def _write_events(
        self,
        user_id: str,
        events: list[dict] | list[EventSpec],
        db: AsyncSession,
    ) -> list[GrowthEvent]:
        """通用事件写入，仅 flush，不 commit。调用方负责 commit + projections。"""
        repo = GrowthEventRepository(db)
        created: list[GrowthEvent] = []
        for spec in events:
            spec = spec  # type: ignore[assignment]
            event = await repo.create_with_dedup(
                user_id=user_id,
                event_type=spec["event_type"],  # type: ignore[typeddict-item]
                entity_type=spec.get("entity_type"),
                entity_id=spec.get("entity_id"),
                payload=spec.get("payload"),
                source=spec.get("source", "system"),
            )
            if event:
                created.append(event)
        if created:
            await db.flush()
        return created

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

        db=None:  自开 session，commit + 同步 .md + async Cognee。
        db=外部:  flush only。调用方 commit 后调 sync_projections()。
        """
        spec: EventSpec = {
            "event_type": event_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": payload,
            "source": source,
        }

        if db is not None:
            created = await self._write_events(user_id, [spec], db)
            return created[0] if created else None

        async with get_async_session_maker()() as session:
            created = await self._write_events(user_id, [spec], session)
            if created:
                await session.commit()
                await self.flush_projections(user_id, [str(created[0].id)])  # type: ignore[attr-defined]
            return created[0] if created else None

    async def remember_batch(
        self,
        user_id: str,
        events: list[EventSpec],
        *,
        db: AsyncSession | None = None,
    ) -> list[GrowthEvent]:
        """批量写入，单事务。

        db=None: 自开 session，一次 commit + 同步全部投影。
        db=外部: flush only。调用方 commit 后调 sync_projections()。
        """
        specs: list[dict] = [dict(e) for e in events]

        if db is not None:
            return await self._write_events(user_id, specs, db)

        async with get_async_session_maker()() as session:
            created = await self._write_events(user_id, specs, session)
            if created:
                await session.commit()
                await self.flush_projections(user_id, [str(e.id) for e in created])  # type: ignore[attr-defined]
            return created
