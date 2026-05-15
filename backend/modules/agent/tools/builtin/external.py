"""数据源读取工具 Handlers — data_source_search / list / get_item / status。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, text

from backend.core.db import get_async_session_maker
from backend.core.logging import get_logger
from backend.modules.agent.tools.core.context import ToolRuntimeContext
from backend.modules.data_sources.models import DataSource
from backend.modules.memory.search import search_all

logger = get_logger(__name__)


async def handle_data_source_search(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """搜索用户已连接的数据源，返回可引用结果。"""

    query = args.get("query", "").strip()
    limit = min(int(args.get("limit", 5)), 10)
    if not query:
        return "[工具错误] 请提供搜索关键词。"

    results = await search_all(ctx.user_id, query, limit=limit)
    if not results:
        return "未找到相关外部文档。"

    lines = [f"找到 {len(results)} 条数据源结果："]
    for idx, item in enumerate(results, 1):
        lines.append(f"\n{idx}. {item.content}")

    return "\n".join(lines)


async def handle_data_source_list(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """列出当前用户已连接的数据源。"""

    async with get_async_session_maker()() as db:
        rows = (
            (
                await db.execute(
                    select(DataSource)
                    .where(
                        DataSource.user_id == ctx.user_id,
                    )
                    .order_by(DataSource.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

        if not rows:
            return "当前未连接任何数据源。"

        lines = [f"已连接 {len(rows)} 个数据源："]
        for ds in rows:
            caps = ", ".join(ds.capabilities_json or ["scan"])
            sync_info = f"last_sync={ds.last_sync_at.isoformat() if ds.last_sync_at else '无'}"
            error_info = f", error={ds.last_error}" if ds.last_error else ""
            lines.append(f"- {ds.name}: {ds.status}, type={ds.type}, capabilities=[{caps}], {sync_info}{error_info}")
        return "\n".join(lines)


async def handle_data_source_get_item(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """按 item_id 读取外部文档的完整内容。"""

    item_id = args.get("item_id", "").strip()
    max_chars = min(int(args.get("max_chars", 4000)), 10000)
    if not item_id:
        return "[工具错误] 请提供 item_id。"

    async with get_async_session_maker()() as db:
        row = (
            await db.execute(
                text("""
                    SELECT title, uri, content, connector_type, data_source_id, indexed_at
                    FROM external_items
                    WHERE id = :id AND user_id = :uid AND deleted_at IS NULL
                """),
                {"id": item_id, "uid": ctx.user_id},
            )
        ).first()

        if not row:
            return f"未找到 item_id={item_id} 的文档。"

        title, uri, content, ctype, _ds_id, indexed_at = row
        snippet = (content or "")[:max_chars]
        truncated = "…（已截断）" if content and len(content) > max_chars else ""

        return (
            f"标题: {title or '未命名'}\n"
            f"来源: {uri or '未知'}\n"
            f"类型: {ctype or 'unknown'}\n"
            f"索引时间: {indexed_at.isoformat() if indexed_at else '无'}\n"
            f"内容:\n{snippet}{truncated}"
        )


async def handle_data_source_status(args: dict[str, Any], ctx: ToolRuntimeContext) -> str:
    """诊断数据源同步状态。"""

    async with get_async_session_maker()() as db:
        sources = (await db.execute(select(DataSource).where(DataSource.user_id == ctx.user_id))).scalars().all()

        if not sources:
            return "当前未配置任何数据源。"

        lines = ["数据源状态诊断："]
        for ds in sources:
            # 统计该数据源的文件数
            count_row = (
                await db.execute(
                    text("SELECT COUNT(*) FROM external_items WHERE data_source_id = :dsid AND deleted_at IS NULL"),
                    {"dsid": ds.id},
                )
            ).scalar()

            status_icon = "🟢" if ds.status == "active" else "🟡" if ds.status == "paused" else "🔴"
            lines.append(
                f"\n{status_icon} {ds.name} ({ds.type})"
                f"\n  状态: {ds.status}"
                f"\n  已索引文档: {count_row or 0}"
                f"\n  最近同步: {ds.last_sync_at.isoformat() if ds.last_sync_at else '从未同步'}"
            )
            if ds.last_error:
                lines.append(f"  最近错误: {ds.last_error}")

        return "\n".join(lines)
