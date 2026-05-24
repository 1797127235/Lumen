"""Lumen 伙伴系统 API"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from shared.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/partner", tags=["partner"])

_DEFAULT_USER = "demo_user"


class MoodResponse(BaseModel):
    mood: str
    mood_intensity: float
    updated_at: str | None


@router.get("/mood", response_model=MoodResponse)
async def get_current_mood(
    user_id: str = _DEFAULT_USER,
    db: AsyncSession = Depends(get_db),
) -> MoodResponse:
    """获取 Lumen 当前情绪状态"""
    try:
        from sqlalchemy import text

        result = await db.execute(
            text("SELECT mood, mood_intensity, updated_at FROM lumen_state WHERE user_id = :uid"),
            {"uid": user_id},
        )
        row = result.fetchone()
        if row is None:
            return MoodResponse(mood="calm", mood_intensity=0.4, updated_at=None)
        return MoodResponse(
            mood=row[0] or "calm",
            mood_intensity=row[1] or 0.4,
            updated_at=str(row[2]) if row[2] else None,
        )
    except Exception as e:
        logger.warning("获取情绪状态失败", error=str(e))
        return MoodResponse(mood="calm", mood_intensity=0.4, updated_at=None)
