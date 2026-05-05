"""画像 API — 简历上传解析 + 画像查询/更新

注意：已迁移到 .md 文件存储，UserProfile 表已废弃。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.backend.services.memory_service import read_memory, write_memory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])


# ── 响应模型 ────────────────────────────────────────


class ProfileResponse(BaseModel):
    """画像响应"""

    content: str  # memory.md 的完整内容


class ProfileUpdate(BaseModel):
    """画像更新请求"""

    content: str  # 要更新的内容


# ── API 端点 ────────────────────────────────────────


@router.get("/me", response_model=ProfileResponse)
async def get_my_profile(
    user_id: str = Query("demo_user"),
):
    """获取当前用户画像（从 memory.md 读取）"""
    try:
        content = read_memory()
        return ProfileResponse(content=content)
    except Exception:
        logger.exception("读取画像失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="读取画像失败")


@router.patch("/me", response_model=ProfileResponse)
async def patch_my_profile(
    patch: ProfileUpdate,
    user_id: str = Query("demo_user"),
):
    """局部更新用户画像（写入 memory.md）"""
    try:
        write_memory(patch.content)
        return ProfileResponse(content=patch.content)
    except Exception:
        logger.exception("更新画像失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="更新画像失败")


@router.delete("/me", response_model=ProfileResponse)
async def reset_my_profile(
    user_id: str = Query("demo_user"),
):
    """清空用户画像（重置 memory.md）"""
    try:
        from app.backend.services.memory_service import _default_memory_template

        default_content = _default_memory_template()
        write_memory(default_content)
        return ProfileResponse(content=default_content)
    except Exception:
        logger.exception("重置画像失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="重置画像失败")


@router.post("/resume")
async def upload_resume(
    file: UploadFile = File(...),
    user_id: str = Query("demo_user"),
):
    """上传简历（PDF/DOCX/TXT/MD），LLM 解析后直接写入 memory.md"""
    try:
        from app.backend.services.profile_service import process_resume_to_memory

        result = await process_resume_to_memory(file, user_id=user_id)
        return result
    except HTTPException:
        raise
    except Exception:
        logger.exception("简历上传解析失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="解析失败，请稍后重试")
