"""画像 API — 简历上传解析 + 画像查询/更新"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.db.session import get_db
from app.backend.schemas.profile import (
    ProfileResponse,
    ProfileUpdate,
    ResumeUploadResponse,
)
from app.backend.services.profile_service import (
    get_profile,
    process_resume,
    update_profile,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])


@router.post("/resume", response_model=ResumeUploadResponse)
async def upload_resume(
    file: UploadFile = File(...),
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """上传简历（PDF/DOCX/TXT/MD），LLM 解析后写入用户画像"""
    try:
        return await process_resume(db, user_id, file)
    except HTTPException:
        raise
    except Exception:
        logger.exception("简历上传解析失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="解析失败，请稍后重试")


@router.get("/me", response_model=ProfileResponse)
async def get_my_profile(
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户画像"""
    try:
        return await get_profile(db, user_id)
    except HTTPException:
        raise
    except Exception:
        logger.exception("读取画像失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="读取画像失败")


@router.patch("/me", response_model=ProfileResponse)
async def patch_my_profile(
    patch: ProfileUpdate,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """局部更新用户画像"""
    try:
        return await update_profile(db, user_id, patch)
    except HTTPException:
        raise
    except Exception:
        logger.exception("更新画像失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="更新画像失败")
