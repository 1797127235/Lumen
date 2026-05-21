"""PresenceStore — 追踪用户消息时间和推送时间"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from shared.logging import get_logger

logger = get_logger(__name__)


async def record_user_message(db: AsyncSession, user_id: str) -> None:
    """用户发消息时，在同一 DB session 中更新 last_user_at（由 save_user_message 调用）"""
    try:
        from sqlalchemy import text

        await db.execute(
            text("""
            INSERT INTO lumen_presence (user_id, last_user_at, updated_at)
            VALUES (:uid, :now, :now)
            ON CONFLICT(user_id) DO UPDATE SET
                last_user_at = :now,
                updated_at = :now
        """),
            {"uid": user_id, "now": datetime.now(UTC)},
        )
        # 注意：不在此 commit，由调用方（save_user_message）统一 commit
    except Exception as e:
        logger.warning("presence 更新失败（不阻断用户消息）", error=str(e))
