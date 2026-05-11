"""关系型数据存储 — Repository 基类 + GrowthEvent Repository。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Generic, TypeVar

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.domain.models import GrowthEvent
from backend.logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def _make_payload_hash(payload: dict | None) -> str | None:
    if not payload:
        return None
    content = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _make_dedupe_key(
    event_type: str,
    entity_type: str | None,
    entity_id: str | None,
    payload_hash: str | None,
) -> str:
    raw_key = "|".join(
        [
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

    async def create(self, **kwargs: Any) -> T:
        instance = self.model(**kwargs)
        self.db.add(instance)
        await self.db.flush()
        return instance

    async def get_by_id(self, id: int) -> T | None:
        result = await self.db.execute(select(self.model).where(self.model.id == id))
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: str, **filters: Any) -> list[T]:
        stmt = select(self.model).where(self.model.user_id == user_id)
        for key, value in filters.items():
            if value is not None:
                stmt = stmt.where(getattr(self.model, key) == value)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

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
    ) -> GrowthEvent | None:
        payload_hash = _make_payload_hash(payload)
        dedupe_key = _make_dedupe_key(event_type, entity_type, entity_id, payload_hash)

        existing = await self.db.execute(select(GrowthEvent.id).where(GrowthEvent.dedupe_key == dedupe_key))
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
            dedupe_key=dedupe_key,
            payload_hash=payload_hash,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    _FTS_TRIGGERS = (
        "trg_growth_events_ad",
        "trg_growth_events_tri_ad",
        "trg_growth_events_au",
        "trg_growth_events_tri_au",
    )

    async def drop_fts_triggers(self) -> None:
        for name in self._FTS_TRIGGERS:
            await self.db.execute(text(f"DROP TRIGGER IF EXISTS {name}"))

    async def rebuild_fts_index(self) -> None:
        for name in self._FTS_TRIGGERS:
            await self.db.execute(text(f"DROP TRIGGER IF EXISTS {name}"))

        await self.db.execute(text("DROP TABLE IF EXISTS growth_events_fts"))
        await self.db.execute(text("DROP TABLE IF EXISTS growth_events_fts_trigram"))
        await self.db.execute(
            text("CREATE VIRTUAL TABLE growth_events_fts USING fts5(event_type, entity_type, entity_id, payload_json)")
        )
        await self.db.execute(
            text(
                "CREATE VIRTUAL TABLE growth_events_fts_trigram USING fts5("
                "event_type, entity_type, entity_id, payload_json, tokenize='trigram')"
            )
        )

        for tbl in ("growth_events_fts", "growth_events_fts_trigram"):
            await self.db.execute(
                text(
                    f"INSERT INTO {tbl}(rowid, event_type, entity_type, entity_id, payload_json) "
                    "SELECT rowid, event_type, entity_type, entity_id, COALESCE(payload_json, '') FROM growth_events"
                )
            )

        await self.db.execute(
            text(
                "CREATE TRIGGER trg_growth_events_ad AFTER DELETE ON growth_events BEGIN "
                "INSERT INTO growth_events_fts(growth_events_fts, rowid, event_type, entity_type, entity_id, payload_json) "
                "VALUES ('delete', old.rowid, old.event_type, old.entity_type, old.entity_id, old.payload_json); END"
            )
        )
        await self.db.execute(
            text(
                "CREATE TRIGGER trg_growth_events_tri_ad AFTER DELETE ON growth_events BEGIN "
                "INSERT INTO growth_events_fts_trigram(growth_events_fts_trigram, rowid, event_type, entity_type, entity_id, payload_json) "
                "VALUES ('delete', old.rowid, old.event_type, old.entity_type, old.entity_id, old.payload_json); END"
            )
        )
        await self.db.execute(
            text(
                "CREATE TRIGGER trg_growth_events_au AFTER UPDATE ON growth_events BEGIN "
                "INSERT INTO growth_events_fts(growth_events_fts, rowid, event_type, entity_type, entity_id, payload_json) "
                "VALUES ('delete', old.rowid, old.event_type, old.entity_type, old.entity_id, old.payload_json); "
                "INSERT INTO growth_events_fts(rowid, event_type, entity_type, entity_id, payload_json) "
                "VALUES (new.rowid, new.event_type, new.entity_type, new.entity_id, new.payload_json); END"
            )
        )
        await self.db.execute(
            text(
                "CREATE TRIGGER trg_growth_events_tri_au AFTER UPDATE ON growth_events BEGIN "
                "INSERT INTO growth_events_fts_trigram(growth_events_fts_trigram, rowid, event_type, entity_type, entity_id, payload_json) "
                "VALUES ('delete', old.rowid, old.event_type, old.entity_type, old.entity_id, old.payload_json); "
                "INSERT INTO growth_events_fts_trigram(rowid, event_type, entity_type, entity_id, payload_json) "
                "VALUES (new.rowid, new.event_type, new.entity_type, new.entity_id, new.payload_json); END"
            )
        )

        logger.info("FTS index rebuilt")

    async def get_batch(self, event_ids: list[str], user_id: str | None = None) -> list[GrowthEvent]:
        if not event_ids:
            return []
        stmt = select(GrowthEvent).where(GrowthEvent.id.in_(event_ids))
        if user_id:
            stmt = stmt.where(GrowthEvent.user_id == user_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_all_by_user(self, user_id: str) -> list[GrowthEvent]:
        result = await self.db.execute(
            select(GrowthEvent).where(GrowthEvent.user_id == user_id).order_by(GrowthEvent.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_needing_projection(
        self, user_id: str, projection_field: str = "projected_cognee_at", limit: int = 50
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
