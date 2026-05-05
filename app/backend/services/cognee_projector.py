"""Projection helpers from growth events to Cognee."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import select

from app.backend.db.base import get_async_session_maker
from app.backend.models.growth_event import GrowthEvent
from app.backend.services import cognee_service

logger = logging.getLogger(__name__)


async def project_event(event: GrowthEvent) -> bool:
    try:
        content = _build_memory_content(event)
        metadata = {
            "user_id": event.user_id,
            "event_id": str(event.id),
            "event_type": event.event_type,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "source": event.source,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        return await cognee_service.remember(
            user_id=event.user_id,
            content=content,
            metadata=metadata,
        )
    except Exception as exc:
        logger.error("Event projection failed: event_id=%s, error=%s", event.id, exc)
        return False


async def project_all_events(user_id: str) -> bool:
    try:
        return await cognee_service.rebuild_from_sqlite(user_id)
    except Exception as exc:
        logger.error("Full projection failed: user_id=%s, error=%s", user_id, exc)
        return False


async def project_new_events(user_id: str, since: datetime | None = None) -> int:
    try:
        async with get_async_session_maker()() as db:
            query = select(GrowthEvent).where(GrowthEvent.user_id == user_id)
            if since:
                query = query.where(GrowthEvent.created_at > since)
            query = query.order_by(GrowthEvent.created_at)
            result = await db.execute(query)
            events = result.scalars().all()

            success_count = 0
            for event in events:
                if await project_event(event):
                    event.projected_cognee_at = datetime.utcnow()
                    success_count += 1
            await db.commit()
            return success_count
    except Exception as exc:
        logger.error("Incremental projection failed: user_id=%s, error=%s", user_id, exc)
        return 0


async def project_event_ids(event_ids: list[str]) -> int:
    if not event_ids:
        return 0
    try:
        async with get_async_session_maker()() as db:
            result = await db.execute(select(GrowthEvent).where(GrowthEvent.id.in_(event_ids)))
            events = list(result.scalars().all())
            success_count = 0
            for event in events:
                if await project_event(event):
                    event.projected_cognee_at = datetime.utcnow()
                    success_count += 1
            await db.commit()
            return success_count
    except Exception as exc:
        logger.error("Event-id projection failed: count=%d, error=%s", len(event_ids), exc)
        return 0


def _build_memory_content(event: GrowthEvent) -> str:
    payload = {}
    if event.payload_json:
        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            payload = {"raw": event.payload_json}

    if event.event_type == "profile_updated":
        if payload.get("memory_md"):
            return "用户更新了核心画像"
        school = payload.get("school_name", "未知学校")
        major = payload.get("major", "未知专业")
        grade = payload.get("grade", "未知年级")
        return f"用户更新了个人画像：{school} {major} {grade}"
    if event.event_type == "skill_added":
        skill = payload.get("skill_name") or payload.get("name") or event.entity_id or "未知技能"
        level = payload.get("level", "未知水平")
        return f"用户掌握了 {skill}（{level}）"
    if event.event_type == "skill_level_changed":
        skill = payload.get("skill_name") or payload.get("name") or event.entity_id or "未知技能"
        old_level = payload.get("old_level", "未知")
        new_level = payload.get("new_level", payload.get("level", "未知"))
        return f"用户 {skill} 水平从 {old_level} 变为 {new_level}"
    if event.event_type == "resume_uploaded":
        return "用户上传了简历"
    if payload:
        return f"{event.event_type}: {json.dumps(payload, ensure_ascii=False)}"
    return f"{event.event_type}: {event.entity_type or 'unknown'}"
