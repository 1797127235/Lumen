"""岗位追踪 HTTP API。

路由前缀为 `/targets`；在 main.py 中与全局 `prefix="/api"` 组合后，
完整路径形如：`POST /api/targets`、`GET /api/targets/board` 等（以项目实际挂载为准）。

认证说明（MVP）：与画像模块一致，通过 Query `user_id` 区分用户；生产环境应改为 JWT。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.db.session import get_db
from app.backend.schemas.target import (
    BoardResponse,
    TargetCreate,
    TargetDetail,
    TargetUpdate,
)
from app.backend.services.target_service import (
    create_target,
    delete_target,
    generate_advice,
    get_board,
    get_target,
    update_target,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/targets", tags=["targets"])


@router.post("", response_model=TargetDetail)
async def create(
    req: TargetCreate,
    background_tasks: BackgroundTasks,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """创建岗位卡片。

    入参三选一（详见 schemas.target.TargetCreate）：
    - diagnosis_id：复用既有诊断，不再调 LLM。
    - jd_text：同步写诊断并关联，响应里 diagnosis 非空。
    - 都不传：仅建卡。

    任何分支都会异步排队 `generate_advice`，首屏 agent_advice 可能仍为空。
    """
    try:
        detail = await create_target(db, user_id, req)
        background_tasks.add_task(generate_advice, detail.target_id, user_id)
        return detail
    except HTTPException:
        raise
    except Exception:
        logger.exception("创建岗位失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="创建岗位失败")


@router.post("/{target_id}/regenerate-advice", response_model=TargetDetail)
async def regenerate_advice_route(
    target_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """手动触发行动建议生成（后台任务）。用于首次后台失败、reload 杀进程等场景。

    返回当前详情（agent_advice 可能仍为旧值）；前端应在数秒内轮询 GET 详情直至更新。
    """
    try:
        detail = await get_target(db, user_id, target_id)
        background_tasks.add_task(generate_advice, target_id, user_id)
        return detail
    except HTTPException:
        raise
    except Exception:
        logger.exception("排队生成建议失败: target_id=%s", target_id)
        raise HTTPException(status_code=500, detail="排队生成建议失败")


@router.get("/board", response_model=BoardResponse)
async def board(
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """看板总览：columns（按状态分组的卡片列表）+ stats（总数、均分、高频缺口）。"""
    try:
        return await get_board(db, user_id)
    except Exception:
        logger.exception("获取看板失败: user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="获取看板失败")


@router.get("/{target_id}", response_model=TargetDetail)
async def detail(
    target_id: str,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """单卡详情：含 JD 字段、备注、完整 diagnosis 字典。"""
    try:
        return await get_target(db, user_id, target_id)
    except HTTPException:
        raise
    except Exception:
        logger.exception("获取岗位详情失败: target_id=%s", target_id)
        raise HTTPException(status_code=500, detail="获取岗位详情失败")


@router.patch("/{target_id}", response_model=TargetDetail)
async def update(
    target_id: str,
    patch: TargetUpdate,
    background_tasks: BackgroundTasks,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """更新岗位；若 status 变更则异步重新生成 agent_advice（无诊断也走精简 prompt）。"""
    try:
        detail, needs_advice = await update_target(db, user_id, target_id, patch)
        if needs_advice:
            background_tasks.add_task(generate_advice, target_id, user_id)
        return detail
    except HTTPException:
        raise
    except Exception:
        logger.exception("更新岗位失败: target_id=%s", target_id)
        raise HTTPException(status_code=500, detail="更新岗位失败")


@router.delete("/{target_id}")
async def delete(
    target_id: str,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """删除岗位卡片（硬删除）。成功返回 {\"deleted\": true}。"""
    try:
        await delete_target(db, user_id, target_id)
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception:
        logger.exception("删除岗位失败: target_id=%s", target_id)
        raise HTTPException(status_code=500, detail="删除岗位失败")
