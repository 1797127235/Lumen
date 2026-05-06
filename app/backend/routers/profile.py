"""Profile routes backed by growth events and markdown projection."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete

from app.backend.db.base import get_async_session_maker
from app.backend.models.growth_event import GrowthEvent
from app.backend.services.cognee_projector import project_all_events, project_event_ids
from app.backend.services.cognee_service import clear_user_index
from app.backend.services.growth_event_service import create_growth_event_with_dedup
from app.backend.services.md_projector import sync_user_md_projection
from app.backend.services.memory_service import read_memory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileResponse(BaseModel):
    content: str


class ProfileUpdate(BaseModel):
    content: str


@router.get("/me", response_model=ProfileResponse)
async def get_my_profile(user_id: str = Query("demo_user")):
    try:
        content = read_memory(user_id)
        if not content.strip():
            projected = await sync_user_md_projection(user_id)
            if projected:
                content = read_memory(user_id)
        return ProfileResponse(content=content)
    except Exception:
        logger.exception("Read profile failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="读取画像失败")


@router.patch("/me", response_model=ProfileResponse)
async def patch_my_profile(
    patch: ProfileUpdate,
    user_id: str = Query("demo_user"),
):
    try:
        async with get_async_session_maker()() as db:
            event = await create_growth_event_with_dedup(
                db=db,
                user_id=user_id,
                event_type="profile_updated",
                entity_type="profile",
                entity_id="memory_md",
                payload={"memory_md": patch.content},
                source="用户主动",
            )
            await db.commit()

        projected = await sync_user_md_projection(user_id)
        if not projected:
            raise HTTPException(status_code=500, detail="画像事件已保存，但 markdown 投影失败")

        if event:
            await project_event_ids([str(event.id)])

        return ProfileResponse(content=read_memory(user_id))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Patch profile failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="更新画像失败")


@router.delete("/me", response_model=ProfileResponse)
async def reset_my_profile(user_id: str = Query("demo_user")):
    try:
        async with get_async_session_maker()() as db:
            await db.execute(
                delete(GrowthEvent).where(
                    GrowthEvent.user_id == user_id,
                    GrowthEvent.entity_type == "profile",
                )
            )
            await db.commit()

        projected = await sync_user_md_projection(user_id)
        if not projected:
            raise HTTPException(status_code=500, detail="画像已重置，但 markdown 投影失败")

        if await clear_user_index(user_id):
            await project_all_events(user_id)

        return ProfileResponse(content=read_memory(user_id))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Reset profile failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="重置画像失败")


@router.post("/resume")
async def upload_resume(
    file: UploadFile = File(...),
    user_id: str = Query("demo_user"),
):
    try:
        from app.backend.services.profile_service import process_resume_to_memory

        return await process_resume_to_memory(file, user_id=user_id)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Resume upload failed: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="解析失败，请稍后重试")
