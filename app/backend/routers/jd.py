"""JD 诊断 API — 岗位描述匹配度诊断"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.db.session import get_db
from app.backend.schemas.jd import JDDiagnoseRequest, JDDiagnoseResponse
from app.backend.services.jd_service import (
    delete_diagnosis,
    diagnose_jd,
    get_diagnosis,
    get_history,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jd", tags=["jd"])


@router.post("/diagnose", response_model=JDDiagnoseResponse)
async def diagnose(
    req: JDDiagnoseRequest,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """提交一段 JD 文本，返回岗位匹配度诊断报告"""
    try:
        return await diagnose_jd(db, user_id, req.jd_text)
    except HTTPException:
        raise
    except Exception:
        logger.exception("JD 诊断失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="JD 诊断失败")


@router.get("/history")
async def jd_history(
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """获取诊断历史（LIMIT 50）"""
    try:
        items = await get_history(db, user_id)
        return {"items": items}
    except Exception:
        logger.exception("获取诊断历史失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="获取诊断历史失败")


@router.get("/{diagnosis_id}", response_model=JDDiagnoseResponse)
async def get_jd_diagnosis(
    diagnosis_id: str,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """获取单条诊断详情"""
    try:
        return await get_diagnosis(db, user_id, diagnosis_id)
    except HTTPException:
        raise
    except Exception:
        logger.exception("获取诊断详情失败: diagnosis_id=%s", diagnosis_id)
        raise HTTPException(status_code=500, detail="获取诊断详情失败")


@router.delete("/{diagnosis_id}")
async def delete_jd_diagnosis(
    diagnosis_id: str,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """删除单条诊断"""
    try:
        await delete_diagnosis(db, user_id, diagnosis_id)
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception:
        logger.exception("删除诊断失败: diagnosis_id=%s", diagnosis_id)
        raise HTTPException(status_code=500, detail="删除诊断失败")
