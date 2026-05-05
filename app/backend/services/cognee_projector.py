"""Cognee Projector — SQLite → Cognee 投影

职责：
- 监听或消费 growth_events
- 把事件转换成 Cognee 可接收的数据
- 更新 Student / Skill / Milestone 等图谱实体
- 支持全量重放重建
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import select

from app.backend.db.base import get_async_session_maker
from app.backend.models.growth_event import GrowthEvent
from app.backend.services import cognee_service

logger = logging.getLogger(__name__)


async def project_event(event: GrowthEvent) -> bool:
    """单个事件投影到 Cognee

    Args:
        event: 成长事件

    Returns:
        bool: 是否成功
    """
    try:
        # 构建记忆内容
        content = _build_memory_content(event)

        # 构建元数据
        metadata = {
            "user_id": event.user_id,
            "event_id": str(event.id),
            "event_type": event.event_type,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "source": event.source,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }

        # 写入 Cognee
        return await cognee_service.remember(
            user_id=event.user_id,
            content=content,
            metadata=metadata,
        )
    except Exception as exc:
        logger.error("Event projection failed: event_id=%s, error=%s", event.id, exc)
        return False


async def project_all_events(user_id: str) -> bool:
    """全量重放重建：从 SQLite 重建 Cognee 图谱

    Args:
        user_id: 用户 ID

    Returns:
        bool: 是否成功
    """
    try:
        # 使用 cognee_service 的重建功能
        return await cognee_service.rebuild_from_sqlite(user_id)
    except Exception as exc:
        logger.error("Full projection failed: user_id=%s, error=%s", user_id, exc)
        return False


async def project_new_events(user_id: str, since: datetime | None = None) -> int:
    """增量投影：投影指定时间之后的新事件

    Args:
        user_id: 用户 ID
        since: 起始时间（None 则投影所有）

    Returns:
        int: 成功投影的事件数量
    """
    try:
        async with get_async_session_maker()() as db:
            query = select(GrowthEvent).where(GrowthEvent.user_id == user_id)

            if since:
                query = query.where(GrowthEvent.created_at > since)

            query = query.order_by(GrowthEvent.created_at)
            result = await db.execute(query)
            events = result.scalars().all()

            success_count = 0
            for event in events:
                if await project_event(event):
                    success_count += 1

            logger.info(
                "Incremental projection: user_id=%s, total=%d, success=%d",
                user_id,
                len(events),
                success_count,
            )
            return success_count
    except Exception as exc:
        logger.error("Incremental projection failed: user_id=%s, error=%s", user_id, exc)
        return 0


def _build_memory_content(event: GrowthEvent) -> str:
    """构建记忆内容

    根据事件类型构建人类可读的记忆文本。
    """
    payload = {}
    if event.payload_json:
        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            payload = {"raw": event.payload_json}

    # 根据事件类型构建不同的记忆内容
    if event.event_type == "profile_updated":
        school = payload.get("school_name", "未知学校")
        major = payload.get("major", "未知专业")
        grade = payload.get("grade", "未知年级")
        return f"用户更新了个人画像：{school} {major} {grade}"

    elif event.event_type == "skill_added":
        skill = payload.get("skill_name", event.entity_id or "未知技能")
        level = payload.get("level", "未知水平")
        return f"用户掌握了 {skill}（{level}）"

    elif event.event_type == "skill_level_changed":
        skill = payload.get("skill_name", event.entity_id or "未知技能")
        old_level = payload.get("old_level", "未知")
        new_level = payload.get("new_level", "未知")
        return f"用户 {skill} 水平从 {old_level} 提升到 {new_level}"

    elif event.event_type == "target_created":
        company = payload.get("company", "未知公司")
        position = payload.get("position", "未知岗位")
        return f"用户创建了求职目标：{company} {position}"

    elif event.event_type == "target_status_changed":
        company = payload.get("company", "未知公司")
        position = payload.get("position", "未知岗位")
        status = payload.get("status", "未知状态")
        return f"用户 {company} {position} 目标状态变更为：{status}"

    elif event.event_type == "reflection_added":
        title = payload.get("title", "无标题")
        return f"用户添加了反思：{title}"

    elif event.event_type == "project_added":
        name = payload.get("name", "未知项目")
        return f"用户添加了项目：{name}"

    elif event.event_type == "resume_uploaded":
        return "用户上传了简历"

    else:
        # 默认：使用 payload_json 或事件类型
        if payload:
            return f"{event.event_type}: {json.dumps(payload, ensure_ascii=False)}"
        return f"{event.event_type}: {event.entity_type or 'unknown'}"
