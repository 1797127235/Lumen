"""关系型数据存储 — Repository 基类 + GrowthEvent Repository。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Generic, TypeVar

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from lib.memory.models import GrowthEvent
from shared.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def _make_payload_hash(payload: dict | None) -> str | None:
    if not payload:
        return None
    content = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _make_dedupe_key(
    user_id: str,
    event_type: str,
    entity_type: str | None,
    entity_id: str | None,
    payload_hash: str | None,
) -> str:
    raw_key = "|".join(
        [
            user_id,
            event_type or "",
            entity_type or "",
            entity_id or "",
            payload_hash or "",
        ]
    )
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class BaseRepository(Generic[T]):
    model: type[T]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, id: int) -> T | None:
        result = await self.db.execute(select(self.model).where(self.model.id == id))  # type: ignore[attr-defined]
        return result.scalar_one_or_none()

    async def delete(self, id: int) -> bool:
        instance = await self.get_by_id(id)
        if instance is not None:
            await self.db.delete(instance)
            return True
        return False


class GrowthEventRepository(BaseRepository[GrowthEvent]):
    model = GrowthEvent

    async def create_with_dedup(
        self,
        user_id: str,
        event_type: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        payload: dict | None = None,
        source: str = "system",
        source_platform: str = "web",
    ) -> GrowthEvent | None:
        payload_hash = _make_payload_hash(payload)
        dedupe_key = _make_dedupe_key(user_id, event_type, entity_type, entity_id, payload_hash)

        existing = await self.db.execute(
            select(GrowthEvent.id).where(
                (GrowthEvent.dedupe_key == dedupe_key) | (GrowthEvent.original_dedupe_key == dedupe_key)
            )
        )
        if existing.scalar_one_or_none() is not None:
            logger.debug("Skipped duplicate event", user_id=user_id, dedupe_key=dedupe_key)
            return None

        payload_json = json.dumps(payload, ensure_ascii=False) if payload else None
        event = GrowthEvent(
            user_id=user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload_json,
            source=source,
            source_platform=source_platform,
            dedupe_key=dedupe_key,
            payload_hash=payload_hash,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def drop_fts_triggers(self) -> None:
        """删除 GrowthEvent FTS 触发器（批量删除前的安全操作）。"""
        from core.migrations import _GROWTH_EVENTS_FTS_TRIGGER_NAMES

        for name in _GROWTH_EVENTS_FTS_TRIGGER_NAMES:
            await self.db.execute(text(f"DROP TRIGGER IF EXISTS {name}"))

    async def rebuild_fts_index(self) -> None:
        """全量重建 GrowthEvent FTS5 索引。委托给 migrations.py 的共享实现。"""
        from core.migrations import rebuild_growth_events_fts

        await rebuild_growth_events_fts(self.db)

    async def get_batch(self, event_ids: list[str], user_id: str | None = None) -> list[GrowthEvent]:
        if not event_ids:
            return []
        stmt = select(GrowthEvent).where(GrowthEvent.id.in_(event_ids))
        if user_id:
            stmt = stmt.where(GrowthEvent.user_id == user_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_event(
        self,
        event_id: str,
        user_id: str,
        payload: dict | None = None,
    ) -> GrowthEvent | None:
        """更新事件 payload，重新计算 dedupe_key 和 payload_hash。"""
        event = await self.db.get(GrowthEvent, event_id)
        if event is None or event.user_id != user_id:
            return None

        if payload is not None:
            event.payload_json = json.dumps(payload, ensure_ascii=False)
            event.payload_hash = _make_payload_hash(payload)
            event.dedupe_key = _make_dedupe_key(
                user_id, event.event_type, event.entity_type, event.entity_id, event.payload_hash
            )
        event.updated_at = datetime.now(UTC)
        await self.db.flush()
        return event

    async def review_event(
        self,
        event_id: str,
        user_id: str,
        confirmation_status: str,
    ) -> GrowthEvent | None:
        """审核事件：confirmed / rejected。"""
        event = await self.db.get(GrowthEvent, event_id)
        if event is None or event.user_id != user_id:
            return None

        event.confirmation_status = confirmation_status
        event.reviewed_at = datetime.now(UTC)
        await self.db.flush()
        return event

    async def get_needing_projection(
        self, user_id: str, projection_field: str = "projected_provider_at", limit: int = 50
    ) -> list[GrowthEvent]:
        from sqlalchemy import null

        field = getattr(GrowthEvent, projection_field)
        stmt = (
            select(GrowthEvent)
            .where(GrowthEvent.user_id == user_id)
            .where(field.is_(null()))
            .order_by(GrowthEvent.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
