"""记忆管理服务 — 封装所有记忆业务逻辑。

统一通过 LumenMemory facade 访问记忆层，不直接操作 stores/projections/DB。
Router 层只负责 HTTP 协议转换，业务逻辑在此层。
"""

from __future__ import annotations

from app.backend.logging_config import get_logger

logger = get_logger(__name__)


async def get_memory_stats(user_id: str) -> dict:
    """获取记忆系统状态与事件数量。

    Returns:
        {"status": str, "count": int}
    """
    from app.backend.memory.facade import get_memory

    memory = get_memory()
    status = memory.cognee_status()
    try:
        count = await memory.count_events(user_id)
    except Exception as exc:
        logger.error("Memory stats count failed: %s", exc)
        count = 0
    return {"status": status, "count": count}


async def reset_memory(user_id: str) -> dict:
    """清空用户记忆（事件表 + .md + Cognee 索引）。"""
    from app.backend.memory.facade import get_memory

    return await get_memory().reset(user_id)


async def list_memories(user_id: str) -> list[dict]:
    """按时间倒序列出用户事件记忆（含 key-value 去重）。"""
    from app.backend.memory.facade import get_memory

    try:
        return await get_memory().list_events(user_id)
    except Exception as exc:
        logger.error("Memory list failed: %s", exc)
        return []


async def search_memories(user_id: str, query: str, limit: int = 10) -> list[dict]:
    """搜索记忆（语义 + FTS5 + .md 兜底）。

    Returns:
        [{"id": str, "memory": str, "created_at": str | None, "categories": [str]}, ...]
    """
    from app.backend.memory.facade import get_memory

    try:
        memory = get_memory()
        items = await memory.recall(user_id, query, limit=limit)
        return [
            {
                "id": item.id,
                "memory": item.content,
                "created_at": item.created_at,
                "categories": item.categories,
            }
            for item in items
        ]
    except Exception as exc:
        logger.error("Memory search failed: %s", exc)
        return []


async def delete_memory(user_id: str, event_id: str) -> tuple[bool, str | None]:
    """删除单条事件记忆并同步投影。

    Returns:
        (success, error_message)
    """
    from app.backend.memory.facade import get_memory

    memory = get_memory()
    success, error = await memory.delete_event(user_id, event_id)
    if success:
        logger.info("Memory deleted: id=%s, user_id=%s", event_id, user_id)
    return success, error


async def rebuild_memory(user_id: str) -> dict:
    """全量重建 .md + Cognee 索引。

    Returns:
        {"md_success": bool, "cognee_success": bool, "index_cleared": bool}
    """
    from app.backend.memory.facade import get_memory

    return await get_memory().rebuild(user_id)


async def compensate_cognee(user_id: str) -> int:
    """补偿扫描：重试 Cognee 投影失败的事件。

    Returns:
        compensated_count: int
    """
    from app.backend.memory.facade import get_memory

    return await get_memory().compensate_cognee(user_id)


async def get_memory_content(user_id: str) -> str:
    """读取用户 .md 画像内容，为空时自动触发投影同步。"""
    from app.backend.memory.facade import get_memory

    return await get_memory().get_memory_content(user_id)
