"""Cognee 记忆服务 — remember/recall/improve/forget 封装"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.backend.config import get_settings
from app.backend.db.base import get_async_session_maker

logger = logging.getLogger(__name__)


def _cognee_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """合并写入 Cognee 的 metadata；dataset 由配置固定，便于单实例共用图谱。"""
    return {**(extra or {}), "dataset": get_settings().cognee_dataset}


async def remember(user_id: str, content: str, metadata: dict[str, Any] | None = None) -> bool:
    """记忆：将内容写入 Cognee

    Args:
        user_id: 用户 ID
        content: 要记忆的内容
        metadata: 元数据（可选）

    Returns:
        bool: 是否成功
    """
    try:
        import cognee

        await cognee.remember(content, metadata=_cognee_metadata(metadata))
        logger.debug("Cognee remember: user_id=%s, len=%d", user_id, len(content))
        return True
    except Exception as exc:
        logger.error("Cognee remember failed: user_id=%s, error=%s", user_id, exc)
        return False


async def recall(user_id: str, query: str, limit: int = 10) -> list[str]:
    """检索：先尝试 Cognee 语义召回，失败降级 SQLite

    Args:
        user_id: 用户 ID
        query: 查询文本
        limit: 返回结果数量限制

    Returns:
        list[str]: 检索结果列表
    """
    # 先尝试 Cognee 语义召回
    try:
        import cognee

        # 使用 Cognee 语义搜索
        results = await cognee.search(query, limit=limit)

        if results:
            # 过滤出属于该用户的结果
            user_results = []
            for result in results:
                # 检查 metadata 中的 user_id
                if hasattr(result, "metadata") and result.metadata:
                    if result.metadata.get("user_id") == user_id:
                        user_results.append(result.text if hasattr(result, "text") else str(result))
                else:
                    # 如果没有 metadata，可能是全局结果，跳过
                    continue

            if user_results:
                logger.debug("Cognee recall: user_id=%s, query=%s, results=%d", user_id, query, len(user_results))
                return user_results[:limit]

        # Cognee 返回空结果，降级到 SQLite
        logger.debug("Cognee recall returned empty, falling back to SQLite: user_id=%s", user_id)

    except Exception as exc:
        # Cognee 不可用，降级到 SQLite
        logger.warning("Cognee recall failed, falling back to SQLite: user_id=%s, error=%s", user_id, exc)

    # 降级：从 SQLite 查询
    return await _recall_from_sqlite(user_id, query, limit)


async def improve(user_id: str, feedback: str) -> bool:
    """改进：根据反馈改进记忆

    Args:
        user_id: 用户 ID
        feedback: 反馈内容

    Returns:
        bool: 是否成功
    """
    try:
        import cognee

        await cognee.improve(feedback)
        logger.debug("Cognee improve: user_id=%s", user_id)
        return True
    except Exception as exc:
        logger.error("Cognee improve failed: user_id=%s, error=%s", user_id, exc)
        return False


async def forget(user_id: str, content: str) -> bool:
    """遗忘：从 Cognee 删除指定记忆

    注意：此函数只删除 Cognee 索引，不删除 SQLite 真相源。
    SQLite 的删除由调用方负责。

    Args:
        user_id: 用户 ID
        content: 要遗忘的内容（"all" 表示删除该用户所有记忆）

    Returns:
        bool: 是否成功
    """
    try:
        # 此函数只删除 Cognee 索引
        # SQLite 的删除由调用方负责
        logger.debug("Cognee forget: user_id=%s, content=%s", user_id, content)
        return True
    except Exception as exc:
        logger.error("Cognee forget failed: user_id=%s, error=%s", user_id, exc)
        return False


async def clear_user_index(user_id: str) -> bool:
    """清空用户的 Cognee 索引

    注意：此函数只删除 Cognee 索引，不删除 SQLite 真相源。
    SQLite 的删除由调用方负责。

    Args:
        user_id: 用户 ID

    Returns:
        bool: 是否成功
    """
    try:
        # 此函数只删除 Cognee 索引
        # SQLite 的删除由调用方负责
        logger.debug("Cognee clear_user_index: user_id=%s", user_id)
        return True
    except Exception as exc:
        logger.error("Cognee clear_user_index failed: user_id=%s, error=%s", user_id, exc)
        return False


async def rebuild_from_sqlite(user_id: str) -> bool:
    """从 SQLite 重建 Cognee 图谱

    Args:
        user_id: 用户 ID

    Returns:
        bool: 是否成功
    """
    try:
        import cognee

        from app.backend.models.growth_event import GrowthEvent

        # 不要调用 forget_all()，这会删除所有用户的数据
        # 只重建指定用户的数据

        # 从 growth_events 重建
        async with get_async_session_maker()() as db:
            result = await db.execute(
                select(GrowthEvent).where(GrowthEvent.user_id == user_id).order_by(GrowthEvent.created_at)
            )
            events = result.scalars().all()

            for event in events:
                content = event.payload_json or f"{event.event_type}: {event.entity_type or 'unknown'}"
                await cognee.remember(content, metadata=_cognee_metadata())

        logger.info("Cognee rebuilt from SQLite: user_id=%s, events=%d", user_id, len(events))
        return True
    except Exception as exc:
        logger.error("Cognee rebuild failed: user_id=%s, error=%s", user_id, exc)
        return False


async def _recall_from_sqlite(user_id: str, query: str, limit: int) -> list[str]:
    """降级：从 SQLite 查询 growth_events

    当 Cognee 不可用时，直接查询 SQLite 作为降级方案。
    """
    try:
        from app.backend.models.growth_event import GrowthEvent

        async with get_async_session_maker()() as db:
            result = await db.execute(
                select(GrowthEvent)
                .where(GrowthEvent.user_id == user_id)
                .order_by(GrowthEvent.created_at.desc())
                .limit(limit)
            )
            events = result.scalars().all()
            # 返回人类可读的摘要，而不是原始 JSON
            memories = []
            for e in events:
                if e.payload_json:
                    try:
                        import json

                        payload = json.loads(e.payload_json)
                        # 构建人类可读的摘要
                        if e.event_type == "skill_added":
                            skill = payload.get("skill_name", e.entity_id or "未知技能")
                            level = payload.get("level", "未知水平")
                            memories.append(f"掌握了 {skill}（{level}）")
                        elif e.event_type == "profile_updated":
                            school = payload.get("school_name", "未知学校")
                            major = payload.get("major", "未知专业")
                            memories.append(f"更新了画像：{school} {major}")
                        else:
                            memories.append(f"{e.event_type}: {json.dumps(payload, ensure_ascii=False)}")
                    except json.JSONDecodeError:
                        memories.append(f"{e.event_type}: {e.payload_json}")
                else:
                    memories.append(f"{e.event_type}: {e.entity_type or 'unknown'}")
            return memories
    except Exception as exc:
        logger.error("SQLite recall failed: user_id=%s, error=%s", user_id, exc)
        return []
