"""技能记录 HTTP API。

路由前缀为 `/skills`；在 main.py 中与全局 `prefix="/api"` 组合后，
完整路径形如：`POST /api/skills`、`GET /api/skills` 等。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.db.session import get_db
from app.backend.services.skill_service import (
    create_skill,
    delete_skill,
    get_user_skills,
    update_skill,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillCreate(BaseModel):
    skill_name: str
    proficiency: str | None = None
    context: str | None = None
    source: str = "form"


class SkillUpdate(BaseModel):
    skill_name: str | None = None
    proficiency: str | None = None
    context: str | None = None


class SkillResponse(BaseModel):
    id: str
    user_id: str
    skill_name: str
    proficiency: str | None
    context: str | None
    source: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


@router.post("", response_model=SkillResponse)
async def create(
    req: SkillCreate,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """创建技能记录"""
    try:
        skill = await create_skill(
            db,
            user_id=user_id,
            skill_name=req.skill_name,
            proficiency=req.proficiency,
            context=req.context,
            source=req.source,
        )
        await db.commit()
        return skill
    except Exception:
        logger.exception("创建技能失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="创建技能失败")


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """获取用户所有技能记录"""
    try:
        return await get_user_skills(db, user_id)
    except Exception:
        logger.exception("获取技能列表失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="获取技能列表失败")


@router.patch("/{skill_id}", response_model=SkillResponse)
async def update(
    skill_id: str,
    patch: SkillUpdate,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """更新技能记录"""
    try:
        skill = await update_skill(db, skill_id, **patch.model_dump(exclude_unset=True))
        if not skill:
            raise HTTPException(status_code=404, detail="技能不存在")
        await db.commit()
        return skill
    except HTTPException:
        raise
    except Exception:
        logger.exception("更新技能失败: skill_id=%s", skill_id)
        raise HTTPException(status_code=500, detail="更新技能失败")


@router.delete("/{skill_id}")
async def delete(
    skill_id: str,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """删除技能记录"""
    try:
        ok = await delete_skill(db, skill_id, user_id)
        if not ok:
            raise HTTPException(status_code=404, detail="技能不存在或无权删除")
        await db.commit()
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception:
        logger.exception("删除技能失败: skill_id=%s", skill_id)
        raise HTTPException(status_code=500, detail="删除技能失败")
