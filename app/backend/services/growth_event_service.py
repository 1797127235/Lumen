"""GrowthEvent Service — 成长事件服务

负责写入 growth_events 表，并触发 .md 和 Cognee 投影。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.models.growth_event import GrowthEvent

logger = logging.getLogger(__name__)


def _make_dedupe_key(event_type: str, entity_type: str | None, entity_id: str | None) -> str:
    """生成去重键

    格式: "{event_type}:{entity_type}:{entity_id}"
    用于精确去重，避免 7 天窗口误杀。

    Args:
        event_type: 事件类型
        entity_type: 实体类型（可选）
        entity_id: 实体 ID（可选）

    Returns:
        去重键字符串
    """
    parts = [event_type]
    if entity_type:
        parts.append(entity_type)
    if entity_id:
        parts.append(entity_id)
    return ":".join(parts)


def _make_payload_hash(payload: dict | None) -> str | None:
    """生成 payload 的 SHA256 哈希

    用于内容级去重，避免相同内容重复写入。

    Args:
        payload: 事件详情

    Returns:
        SHA256 哈希字符串，如果 payload 为空则返回 None
    """
    if not payload:
        return None
    # 排序确保相同内容生成相同哈希
    content = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def check_duplicate(
    db: AsyncSession,
    user_id: str,
    dedupe_key: str,
    payload_hash: str | None = None,
) -> bool:
    """检查是否存在重复事件

    Args:
        db: 数据库会话
        user_id: 用户 ID
        dedupe_key: 去重键
        payload_hash: payload 哈希（可选）

    Returns:
        是否存在重复
    """
    query = select(func.count(GrowthEvent.id)).where(
        GrowthEvent.user_id == user_id,
        GrowthEvent.dedupe_key == dedupe_key,
    )
    result = await db.execute(query)
    count = result.scalar() or 0
    return count > 0


async def create_growth_event(
    db: AsyncSession,
    user_id: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict | None = None,
    source: str = "system",
    dedupe_key: str | None = None,
    payload_hash: str | None = None,
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
        dedupe_key: 去重键（可选，自动生成）
        payload_hash: payload 哈希（可选，自动生成）

    Returns:
        创建的 GrowthEvent 实例
    """
    # 序列化 payload
    payload_json = None
    if payload:
        payload_json = json.dumps(payload, ensure_ascii=False)

    # 自动生成 dedupe_key 和 payload_hash
    if dedupe_key is None:
        dedupe_key = _make_dedupe_key(event_type, entity_type, entity_id)
    if payload_hash is None:
        payload_hash = _make_payload_hash(payload)

    # 创建事件
    event = GrowthEvent(
        user_id=user_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload_json=payload_json,
        source=source,
        dedupe_key=dedupe_key,
        payload_hash=payload_hash,
    )
    db.add(event)
    await db.flush()

    logger.info(
        "创建成长事件: user_id=%s, event_type=%s, entity_type=%s, entity_id=%s, dedupe_key=%s",
        user_id,
        event_type,
        entity_type,
        entity_id,
        dedupe_key,
    )

    return event


async def create_growth_event_with_dedup(
    db: AsyncSession,
    user_id: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict | None = None,
    source: str = "system",
) -> GrowthEvent | None:
    """创建成长事件（带去重）

    使用 UNIQUE 约束防止 TOCTOU 竞态。
    如果存在相同 dedupe_key 的事件，则跳过创建。

    Args:
        db: 数据库会话
        user_id: 用户 ID
        event_type: 事件类型
        entity_type: 实体类型（可选）
        entity_id: 实体 ID（可选）
        payload: 事件详情（可选）
        source: 事件来源

    Returns:
        创建的 GrowthEvent 实例，如果重复则返回 None
    """
    from sqlalchemy.exc import IntegrityError

    dedupe_key = _make_dedupe_key(event_type, entity_type, entity_id)

    try:
        # 尝试创建事件，如果 dedupe_key 重复会触发 IntegrityError
        event = await create_growth_event(
            db=db,
            user_id=user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            source=source,
            dedupe_key=dedupe_key,
        )
        return event
    except IntegrityError:
        # UNIQUE 约束触发，说明是重复事件
        await db.rollback()
        logger.debug(
            "跳过重复事件（UNIQUE 约束）: user_id=%s, dedupe_key=%s",
            user_id,
            dedupe_key,
        )
        return None


async def mark_projected_md(db: AsyncSession, event_id: str) -> None:
    """标记事件已投影到 .md

    Args:
        db: 数据库会话
        event_id: 事件 ID
    """
    result = await db.execute(select(GrowthEvent).where(GrowthEvent.id == event_id))
    event = result.scalar_one_or_none()
    if event:
        event.projected_md_at = datetime.utcnow()
        await db.flush()
    else:
        logger.warning("Event not found for projection marking (md): %s", event_id)


async def mark_projected_cognee(db: AsyncSession, event_id: str) -> None:
    """标记事件已投影到 Cognee

    Args:
        db: 数据库会话
        event_id: 事件 ID
    """
    result = await db.execute(select(GrowthEvent).where(GrowthEvent.id == event_id))
    event = result.scalar_one_or_none()
    if event:
        event.projected_cognee_at = datetime.utcnow()
        await db.flush()
    else:
        logger.warning("Event not found for projection marking (cognee): %s", event_id)


async def get_unprojected_md_events(db: AsyncSession, user_id: str) -> list[GrowthEvent]:
    """获取未投影到 .md 的事件

    Args:
        db: 数据库会话
        user_id: 用户 ID

    Returns:
        未投影到 .md 的事件列表（按时间正序）
    """
    result = await db.execute(
        select(GrowthEvent)
        .where(
            GrowthEvent.user_id == user_id,
            GrowthEvent.projected_md_at.is_(None),
        )
        .order_by(GrowthEvent.created_at.asc())
    )
    return list(result.scalars().all())


async def get_unprojected_cognee_events(db: AsyncSession, user_id: str) -> list[GrowthEvent]:
    """获取未投影到 Cognee 的事件

    Args:
        db: 数据库会话
        user_id: 用户 ID

    Returns:
        未投影到 Cognee 的事件列表（按时间正序）
    """
    result = await db.execute(
        select(GrowthEvent)
        .where(
            GrowthEvent.user_id == user_id,
            GrowthEvent.projected_cognee_at.is_(None),
        )
        .order_by(GrowthEvent.created_at.asc())
    )
    return list(result.scalars().all())


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
