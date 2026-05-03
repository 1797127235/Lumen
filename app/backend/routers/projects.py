"""项目经历 HTTP API。

路由前缀为 `/projects`；在 main.py 中与全局 `prefix="/api"` 组合后，
完整路径形如：`POST /api/projects`、`GET /api/projects` 等。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.db.session import get_db
from app.backend.services.project_service import (
    create_project,
    delete_project,
    get_user_projects,
    update_project,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    title: str
    tech_stack: str | None = None
    role: str | None = None
    period: str | None = None
    description: str | None = None
    source: str = "form"


class ProjectUpdate(BaseModel):
    title: str | None = None
    tech_stack: str | None = None
    role: str | None = None
    period: str | None = None
    description: str | None = None


class ProjectResponse(BaseModel):
    id: str
    user_id: str
    title: str
    tech_stack: str | None
    role: str | None
    period: str | None
    description: str | None
    source: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


@router.post("", response_model=ProjectResponse)
async def create(
    req: ProjectCreate,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """创建项目经历"""
    try:
        project = await create_project(
            db,
            user_id=user_id,
            title=req.title,
            tech_stack=req.tech_stack,
            role=req.role,
            period=req.period,
            description=req.description,
            source=req.source,
        )
        await db.commit()
        return project
    except Exception:
        logger.exception("创建项目失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="创建项目失败")


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """获取用户所有项目经历"""
    try:
        return await get_user_projects(db, user_id)
    except Exception:
        logger.exception("获取项目列表失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="获取项目列表失败")


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update(
    project_id: str,
    patch: ProjectUpdate,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """更新项目经历"""
    try:
        project = await update_project(db, project_id, **patch.model_dump(exclude_unset=True))
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        await db.commit()
        return project
    except HTTPException:
        raise
    except Exception:
        logger.exception("更新项目失败: project_id=%s", project_id)
        raise HTTPException(status_code=500, detail="更新项目失败")


@router.delete("/{project_id}")
async def delete(
    project_id: str,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """删除项目经历"""
    try:
        ok = await delete_project(db, project_id, user_id)
        if not ok:
            raise HTTPException(status_code=404, detail="项目不存在或无权删除")
        await db.commit()
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception:
        logger.exception("删除项目失败: project_id=%s", project_id)
        raise HTTPException(status_code=500, detail="删除项目失败")
