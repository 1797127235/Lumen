"""Cognee 封装：失败时回退到 SQLite。"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.backend.config import get_settings
from app.backend.db.base import get_async_session_maker

logger = logging.getLogger(__name__)


def _cognee_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {**(extra or {}), "dataset": get_settings().cognee_dataset}


async def remember(user_id: str, content: str, metadata: dict[str, Any] | None = None) -> bool:
    try:
        import cognee

        await cognee.remember(content, metadata=_cognee_metadata(metadata))
        logger.debug("Cognee remember: user_id=%s, len=%d", user_id, len(content))
        return True
    except Exception as exc:
        logger.error("Cognee remember failed: user_id=%s, error=%s", user_id, exc)
        return False


async def recall(user_id: str, query: str, limit: int = 10) -> list[dict[str, str | None]]:
    """语义搜索。返回 [{"text": ..., "event_id": ..., "event_type": ..., "created_at": ...}]。"""
    try:
        import cognee

        results = await cognee.search(query, limit=limit)
        user_results: list[dict] = []
        for result in results or []:
            metadata = getattr(result, "metadata", None) or {}
            if metadata.get("user_id") != user_id:
                continue
            user_results.append(
                {
                    "text": getattr(result, "text", str(result)),
                    "event_id": metadata.get("event_id"),
                    "event_type": metadata.get("event_type"),
                    "created_at": metadata.get("created_at"),
                }
            )
        if user_results:
            return user_results[:limit]
    except Exception as exc:
        logger.warning("Cognee recall failed, fallback to SQLite: user_id=%s, error=%s", user_id, exc)

    return await _recall_from_sqlite(user_id, query, limit)


async def improve(user_id: str, feedback: str) -> bool:
    try:
        import cognee

        await cognee.improve(feedback)
        logger.debug("Cognee improve: user_id=%s", user_id)
        return True
    except Exception as exc:
        logger.error("Cognee improve failed: user_id=%s, error=%s", user_id, exc)
        return False


async def forget(user_id: str, content: str) -> bool:
    logger.debug("Cognee forget requested: user_id=%s, content=%s", user_id, content)
    return True


async def clear_user_index(user_id: str) -> bool:
    try:
        from app.backend.agent.cognee_client import USER_DATA_DIR, init_cognee

        removed_any = False
        for name in ("kuzu", "lancedb"):
            path = Path(USER_DATA_DIR) / name
            if path.exists():
                shutil.rmtree(path, ignore_errors=False)
                removed_any = True

        init_cognee()
        logger.info("Cognee index cleared: user_id=%s, removed_any=%s", user_id, removed_any)
        return True
    except Exception as exc:
        logger.error("Cognee clear_user_index failed: user_id=%s, error=%s", user_id, exc)
        return False


async def rebuild_from_sqlite(user_id: str) -> bool:
    try:
        import cognee

        from app.backend.models.growth_event import GrowthEvent

        async with get_async_session_maker()() as db:
            result = await db.execute(
                select(GrowthEvent).where(GrowthEvent.user_id == user_id).order_by(GrowthEvent.created_at)
            )
            events = result.scalars().all()

            for event in events:
                content = event.payload_json or f"{event.event_type}: {event.entity_type or 'unknown'}"
                metadata = {
                    "user_id": event.user_id,
                    "event_id": str(event.id),
                    "event_type": event.event_type,
                    "entity_type": event.entity_type,
                    "entity_id": event.entity_id,
                    "source": event.source,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
                await cognee.remember(content, metadata=_cognee_metadata(metadata))
                event.projected_cognee_at = datetime.now(datetime.UTC)

            await db.commit()

        logger.info("Cognee rebuilt from SQLite: user_id=%s, events=%d", user_id, len(events))
        return True
    except Exception as exc:
        logger.error("Cognee rebuild failed: user_id=%s, error=%s", user_id, exc)
        return False


async def _recall_from_sqlite(user_id: str, query: str, limit: int) -> list[dict[str, str | None]]:
    try:
        from app.backend.models.growth_event import GrowthEvent

        async with get_async_session_maker()() as db:
            result = await db.execute(
                select(GrowthEvent)
                .where(GrowthEvent.user_id == user_id)
                .order_by(GrowthEvent.created_at.desc())
                .limit(limit)
            )
            events = result.scalars().all()

        memories: list[dict] = []
        for event in events:
            text = _format_event_text(event)
            memories.append(
                {
                    "text": text,
                    "event_id": str(event.id),
                    "event_type": event.event_type,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
            )
        return memories
    except Exception as exc:
        logger.error("SQLite recall failed: user_id=%s, error=%s", user_id, exc)
        return []


def _format_event_text(event) -> str:
    """GrowthEvent → 人类可读文本。"""
    payload = {}
    if event.payload_json:
        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            payload = {}

    if event.event_type == "skill_added":
        skill = payload.get("skill_name") or payload.get("name") or event.entity_id or "未知技能"
        level = payload.get("level", "未知水平")
        return f"掌握了 {skill}（{level}）"
    if event.event_type == "profile_updated":
        if payload.get("memory_md"):
            return "更新了核心画像"
        school = payload.get("school_name", "")
        major = payload.get("major", "")
        return f"更新了画像：{school} {major}".strip()
    if event.event_type == "experience_added":
        title = payload.get("title", event.entity_id or "未知经历")
        desc = payload.get("description", "")
        return f"经历：{title} — {desc}" if desc else f"经历：{title}"
    if payload:
        return f"{event.event_type}: {json.dumps(payload, ensure_ascii=False)}"
    return f"{event.event_type}: {event.entity_type or 'unknown'}"
