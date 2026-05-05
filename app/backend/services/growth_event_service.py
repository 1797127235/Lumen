"""GrowthEvent Service — 成长事件服务

负责写入 growth_events 表，并触发 Cognee 投影。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.models.growth_event import GrowthEvent

logger = logging.getLogger(__name__)


async def create_growth_event(
    db: AsyncSession,
    user_id: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict | None = None,
    source: str = "system",
    project: bool = True,
) -> GrowthEvent:
    """创建成长事件

    Args:
        db: 数据库会话
        user_id: 用户 ID
        event_type: 事件类型（profile_updated, skill_added, goal_updated 等）
        entity_type: 实体类型（profile, skill, goal 等）
        entity_id: 实体 ID
        payload: 事件详情（JSON 序列化）
        source: 事件来源（user主动, 对话识别, 简历提取, 系统产出）
        project: 是否触发 Cognee 投影

    Returns:
        创建的 GrowthEvent 实例
    """
    # 序列化 payload
    payload_json = None
    if payload:
        payload_json = json.dumps(payload, ensure_ascii=False)

    # 创建事件
    event = GrowthEvent(
        user_id=user_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload_json=payload_json,
        source=source,
    )
    db.add(event)
    await db.flush()

    logger.info(
        "创建成长事件: user_id=%s, event_type=%s, entity_type=%s, entity_id=%s",
        user_id,
        event_type,
        entity_type,
        entity_id,
    )

    # 触发 Cognee 投影（fire-and-forget）
    if project:
        try:
            import asyncio

            from app.backend.services.cognee_projector import project_event

            # 异步投影，不阻塞当前事务
            task = asyncio.create_task(project_event(event))
            # 存储任务引用，防止被垃圾回收
            _ = task
        except Exception as e:
            # 投影失败不影响事件写入
            logger.warning("Cognee 投影失败: %s", e)

    return event


async def list_growth_events(
    db: AsyncSession,
    user_id: str,
    limit: int = 100,
    event_type: str | None = None,
    entity_type: str | None = None,
) -> list[GrowthEvent]:
    """列出用户的成长事件

    Args:
        db: 数据库会话
        user_id: 用户 ID
        limit: 返回数量限制
        event_type: 过滤事件类型
        entity_type: 过滤实体类型

    Returns:
        成长事件列表（按时间倒序）
    """
    query = select(GrowthEvent).where(GrowthEvent.user_id == user_id)

    if event_type:
        query = query.where(GrowthEvent.event_type == event_type)
    if entity_type:
        query = query.where(GrowthEvent.entity_type == entity_type)

    query = query.order_by(GrowthEvent.created_at.desc()).limit(limit)

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_growth_event_by_id(
    db: AsyncSession,
    event_id: str,
    user_id: str,
) -> GrowthEvent | None:
    """获取单个成长事件

    Args:
        db: 数据库会话
        event_id: 事件 ID
        user_id: 用户 ID（用于权限校验）

    Returns:
        成长事件实例，如果不存在或不属于该用户则返回 None
    """
    result = await db.execute(
        select(GrowthEvent).where(
            GrowthEvent.id == event_id,
            GrowthEvent.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def delete_growth_events(
    db: AsyncSession,
    user_id: str,
    event_type: str | None = None,
) -> int:
    """删除用户的成长事件

    Args:
        db: 数据库会话
        user_id: 用户 ID
        event_type: 过滤事件类型（可选）

    Returns:
        删除的事件数量
    """
    query = delete(GrowthEvent).where(GrowthEvent.user_id == user_id)

    if event_type:
        query = query.where(GrowthEvent.event_type == event_type)

    result = await db.execute(query)
    return result.rowcount


async def count_growth_events(
    db: AsyncSession,
    user_id: str,
) -> dict[str, int]:
    """统计用户的成长事件

    Args:
        db: 数据库会话
        user_id: 用户 ID

    Returns:
        统计结果，包含总数和各类型数量
    """
    # 总数
    total_result = await db.execute(select(func.count()).where(GrowthEvent.user_id == user_id))
    total = total_result.scalar()

    # 按类型统计
    type_result = await db.execute(
        select(GrowthEvent.event_type, func.count())
        .where(GrowthEvent.user_id == user_id)
        .group_by(GrowthEvent.event_type)
    )
    type_counts = {row[0]: row[1] for row in type_result}

    return {
        "total": total,
        "by_type": type_counts,
    }
