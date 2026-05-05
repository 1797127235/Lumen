"""GrowthEvent helpers for the SQLite truth source."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.models.growth_event import GrowthEvent

logger = logging.getLogger(__name__)


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


async def check_duplicate(
    db: AsyncSession,
    user_id: str,
    dedupe_key: str,
    payload_hash: str | None = None,
) -> bool:
    del payload_hash
    query = select(func.count(GrowthEvent.id)).where(
        GrowthEvent.user_id == user_id,
        GrowthEvent.dedupe_key == dedupe_key,
    )
    result = await db.execute(query)
    count = result.scalar() or 0
    return count > 0


async def create_growth_event(
    db: AsyncSession,
    user_id: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict | None = None,
    source: str = "system",
    dedupe_key: str | None = None,
    payload_hash: str | None = None,
) -> GrowthEvent:
    payload_json = json.dumps(payload, ensure_ascii=False) if payload else None

    if payload_hash is None:
        payload_hash = _make_payload_hash(payload)
    if dedupe_key is None:
        dedupe_key = _make_dedupe_key(event_type, entity_type, entity_id, payload_hash)

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
    db.add(event)
    await db.flush()

    logger.info(
        "Created growth event: user_id=%s, event_type=%s, entity_type=%s, entity_id=%s, dedupe_key=%s",
        user_id,
        event_type,
        entity_type,
        entity_id,
        dedupe_key,
    )
    return event


async def create_growth_event_with_dedup(
    db: AsyncSession,
    user_id: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict | None = None,
    source: str = "system",
) -> GrowthEvent | None:
    payload_hash = _make_payload_hash(payload)
    dedupe_key = _make_dedupe_key(event_type, entity_type, entity_id, payload_hash)

    try:
        async with db.begin_nested():
            event = await create_growth_event(
                db=db,
                user_id=user_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload,
                source=source,
                dedupe_key=dedupe_key,
                payload_hash=payload_hash,
            )
        return event
    except IntegrityError:
        logger.debug(
            "Skipped duplicate event: user_id=%s, dedupe_key=%s",
            user_id,
            dedupe_key,
        )
        return None


async def mark_projected_md(db: AsyncSession, event_id: str) -> None:
    result = await db.execute(select(GrowthEvent).where(GrowthEvent.id == event_id))
    event = result.scalar_one_or_none()
    if event:
        event.projected_md_at = datetime.utcnow()
        await db.flush()
    else:
        logger.warning("Event not found for projection marking (md): %s", event_id)


async def mark_projected_cognee(db: AsyncSession, event_id: str) -> None:
    result = await db.execute(select(GrowthEvent).where(GrowthEvent.id == event_id))
    event = result.scalar_one_or_none()
    if event:
        event.projected_cognee_at = datetime.utcnow()
        await db.flush()
    else:
        logger.warning("Event not found for projection marking (cognee): %s", event_id)


async def get_unprojected_md_events(db: AsyncSession, user_id: str) -> list[GrowthEvent]:
    result = await db.execute(
        select(GrowthEvent)
        .where(
            GrowthEvent.user_id == user_id,
            GrowthEvent.projected_md_at.is_(None),
        )
        .order_by(GrowthEvent.created_at.asc())
    )
    return list(result.scalars().all())


async def get_unprojected_cognee_events(db: AsyncSession, user_id: str) -> list[GrowthEvent]:
    result = await db.execute(
        select(GrowthEvent)
        .where(
            GrowthEvent.user_id == user_id,
            GrowthEvent.projected_cognee_at.is_(None),
        )
        .order_by(GrowthEvent.created_at.asc())
    )
    return list(result.scalars().all())


async def list_growth_events(
    db: AsyncSession,
    user_id: str,
    limit: int = 100,
    event_type: str | None = None,
    entity_type: str | None = None,
) -> list[GrowthEvent]:
    query = select(GrowthEvent).where(GrowthEvent.user_id == user_id)

    if event_type:
        query = query.where(GrowthEvent.event_type == event_type)
    if entity_type:
        query = query.where(GrowthEvent.entity_type == entity_type)

    query = query.order_by(GrowthEvent.created_at.desc()).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_growth_event_by_id(
    db: AsyncSession,
    event_id: str,
    user_id: str,
) -> GrowthEvent | None:
    result = await db.execute(
        select(GrowthEvent).where(
            GrowthEvent.id == event_id,
            GrowthEvent.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def delete_growth_events(
    db: AsyncSession,
    user_id: str,
    event_type: str | None = None,
) -> int:
    query = delete(GrowthEvent).where(GrowthEvent.user_id == user_id)
    if event_type:
        query = query.where(GrowthEvent.event_type == event_type)
    result = await db.execute(query)
    return result.rowcount


async def count_growth_events(
    db: AsyncSession,
    user_id: str,
) -> dict[str, int]:
    total_result = await db.execute(select(func.count()).where(GrowthEvent.user_id == user_id))
    total = total_result.scalar()

    type_result = await db.execute(
        select(GrowthEvent.event_type, func.count())
        .where(GrowthEvent.user_id == user_id)
        .group_by(GrowthEvent.event_type)
    )
    type_counts = {row[0]: row[1] for row in type_result}

    return {
        "total": total,
        "by_type": type_counts,
    }
