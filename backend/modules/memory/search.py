"""统一搜索层 — 从多个存储源召回记忆。

主路径：FTS5 全文搜索（Narrative 事件 + 外部文档）
语义路径：DocumentIndexProvider（Cognee/LanceDB/HRR），统一覆盖全部数据

Profile 事件不走搜索索引 — L0 固定注入已覆盖。"""

from __future__ import annotations

import re as _re
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.core.db import get_async_session_maker
from backend.core.logging import get_logger
from backend.modules.memory.classifier import NARRATIVE_EVENT_TYPES

logger = get_logger(__name__)

_CJK_RE = _re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")

# Provider 结果解析正则
_PROVIDER_RESULT_RE = _re.compile(
    r"\[来源:\s*([^\]]+)\](?:\n|\r\n?)(.*?)(?=\n\n|\[来源:|\Z)",
    _re.DOTALL,
)


class MemoryItem(BaseModel):
    """搜索结果条目。"""

    id: str
    content: str
    created_at: str | None = None
    categories: list[str] = Field(default_factory=list)


async def search_all(
    user_id: str,
    query: str,
    limit: int = 10,
    *,
    datasets: list[str] | None = None,
    include_provider: bool = True,
    source_scope: str = "narrative",  # "narrative" | "external" | "all"
) -> list[MemoryItem]:
    """搜索记忆：FTS5（关键词）+ Provider（语义，统一）。

    include_provider: 默认 True — 通过 DocumentIndexProvider 做语义搜索。
    source_scope: 控制搜索范围 — narrative（默认，仅事件）/ external（仅外部文档）/ all（两者）
    """
    seen: set[str] = set()
    results: list[MemoryItem] = []

    # Provider 语义搜索：统一覆盖 narrative + external
    if include_provider:
        results.extend(await _search_provider(query, limit, seen))

    if source_scope in ("narrative", "all"):
        results.extend(await _search_fts5(user_id, query, limit, seen))

    if source_scope in ("external", "all"):
        results.extend(await _search_external_fts5(query, limit, seen, user_id))

    return results[:limit]


_FTS5_SPECIAL_RE = _re.compile(r'[+\-*"()^@]')


def _sanitize_fts5(query: str) -> str | None:
    """移除 FTS5 保留操作符，避免语法错误（如 C++、C#）。"""
    sanitized = _FTS5_SPECIAL_RE.sub(" ", query).strip()
    return sanitized or None


def _escape_fts5(query: str) -> str | None:
    """安全转义 FTS5 MATCH 查询。

    策略：移除所有 FTS5 操作符和危险字符，只保留中英文、数字、空格，
    然后包裹为字面量词组。
    """
    # 步骤1：sanitize — 移除 FTS5 操作符 + 反斜杠(Windows路径) + 双引号
    cleaned = _FTS5_SPECIAL_RE.sub(" ", query)
    cleaned = cleaned.replace("\\", " ").replace('"', " ")
    cleaned = _re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


async def _search_fts5(user_id: str, query: str, limit: int, seen: set[str]) -> list[MemoryItem]:
    """SQLite FTS5 全文搜索 — 仅搜索 Narrative 事件。"""
    results: list[MemoryItem] = []
    safe_query = _escape_fts5(query)
    if not safe_query:
        return results
    try:
        fts_table = "growth_events_fts_trigram" if _CJK_RE.search(query) else "growth_events_fts"
        async with get_async_session_maker()() as db:
            rows = (
                await db.execute(
                    _fts_query(fts_table),
                    {
                        "uid": user_id,
                        "etypes": tuple(NARRATIVE_EVENT_TYPES),
                        "q": safe_query,
                        "lim": limit,
                    },
                )
            ).all()

            for row in rows:
                eid = str(row[0])
                if eid in seen:
                    continue
                seen.add(eid)
                results.append(
                    MemoryItem(
                        id=eid,
                        content=row[1] or f"{row[2]}: {row[3] or ''}",
                        created_at=row[4].isoformat() if row[4] else None,
                        categories=[row[2]] if row[2] else [],
                    )
                )
    except Exception as exc:
        logger.warning("FTS5 search failed", error=str(exc))
    return results


def _fts_query(table_name: str):
    return text(f"""
        SELECT ge.id, ge.payload_json, ge.event_type, ge.entity_type, ge.created_at
        FROM growth_events ge
        JOIN {table_name} fts ON fts.rowid = ge.rowid
        WHERE ge.user_id = :uid
          AND ge.event_type IN :etypes
          AND {table_name} MATCH :q
        ORDER BY ge.created_at DESC
        LIMIT :lim
    """)


async def _search_external_fts5(query: str, limit: int, seen: set[str], user_id: str | None = None) -> list[MemoryItem]:
    """FTS5 全文搜索 external_items — Phase 2 外部数据。
    CJK 短查询（1-2 字）走 LIKE fallback，因为 trigram tokenizer
    至少需要 3 个字符才能命中。3 字及以上走 FTS5。
    """
    results: list[MemoryItem] = []
    safe_query = _escape_fts5(query)
    if not safe_query:
        return results
    try:
        is_cjk = bool(_CJK_RE.search(query))
        # CJK 短查询（< 3 CJK 字符）→ LIKE fallback
        if is_cjk and _count_cjk_chars(query) < 3:
            return await _search_external_like(query, limit, seen, user_id)

        fts_table = "external_items_fts_trigram" if is_cjk else "external_items_fts"
        params: dict[str, Any] = {"q": safe_query, "lim": limit}
        user_filter = ""
        if user_id:
            user_filter = "AND ei.user_id = :uid AND (ds.status = 'active' OR ds.id IS NULL)"
            params["uid"] = user_id

        async with get_async_session_maker()() as db:
            rows = (
                await db.execute(
                    text(f"""
                        SELECT
                            ei.id, ei.content, ei.connector_type, ei.external_id,
                            ei.uri, ei.title, ei.indexed_at, ei.updated_at,
                            ds.name as source_name
                        FROM external_items ei
                        LEFT JOIN data_sources ds ON ds.id = ei.data_source_id
                        JOIN {fts_table} fts ON fts.rowid = ei.rowid
                        WHERE {fts_table} MATCH :q
                          AND ei.deleted_at IS NULL
                          {user_filter}
                        ORDER BY ei.indexed_at DESC
                        LIMIT :lim
                    """),
                    params,
                )
            ).all()

            for row in rows:
                eid = f"ext:{row[0]}"
                if eid in seen:
                    continue
                seen.add(eid)
                indexed_at = row[6]
                updated_at = row[7]
                source_name = row[8] or row[2] or "未知来源"
                title = row[5] or "未命名文档"
                uri = row[4] or ""
                snippet = row[1][:300] if row[1] else ""
                # 格式化返回内容，包含引用信息
                content = (
                    f"标题: {title}\n"
                    f"来源: {source_name}\n"
                    f"路径: {uri}\n"
                    f"片段: {snippet}\n"
                    f"item_id: {row[0]}"
                )
                results.append(
                    MemoryItem(
                        id=eid,
                        content=content,
                        created_at=(
                            updated_at.isoformat()
                            if updated_at and hasattr(updated_at, "isoformat")
                            else (indexed_at.isoformat() if indexed_at and hasattr(indexed_at, "isoformat") else None)
                        ),
                        categories=[f"external:{source_name}"],
                    )
                )
    except Exception as exc:
        logger.warning("external_fts5 search failed", error=str(exc))
    return results


def _count_cjk_chars(query: str) -> int:
    """统计查询中的 CJK 字符数。"""
    return sum(1 for ch in query if _CJK_RE.match(ch))


async def _search_external_like(query: str, limit: int, seen: set[str], user_id: str | None = None) -> list[MemoryItem]:
    """LIKE fallback — CJK 短查询（1-2 字）时 trigram 无法命中。"""
    results: list[MemoryItem] = []
    # 安全转义 LIKE 通配符（用 ! 作为 ESCAPE 字符，避免反斜杠跨层转义问题）
    escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    like_pattern = "%" + escaped + "%"
    params: dict[str, Any] = {"q": like_pattern, "lim": limit}
    user_filter = ""
    if user_id:
        user_filter = "AND ei.user_id = :uid AND (ds.status = 'active' OR ds.id IS NULL)"
        params["uid"] = user_id

    try:
        async with get_async_session_maker()() as db:
            rows = (
                await db.execute(
                    text(f"""
                        SELECT
                            ei.id, ei.content, ei.connector_type, ei.external_id,
                            ei.uri, ei.title, ei.indexed_at, ei.updated_at,
                            ds.name as source_name
                        FROM external_items ei
                        LEFT JOIN data_sources ds ON ds.id = ei.data_source_id
                        WHERE ei.content LIKE :q ESCAPE '!'
                          AND ei.deleted_at IS NULL
                          {user_filter}
                        ORDER BY ei.indexed_at DESC
                        LIMIT :lim
                    """),
                    params,
                )
            ).all()

            for row in rows:
                eid = f"ext:{row[0]}"
                if eid in seen:
                    continue
                seen.add(eid)
                indexed_at = row[6]
                updated_at = row[7]
                source_name = row[8] or row[2] or "未知来源"
                title = row[5] or "未命名文档"
                uri = row[4] or ""
                snippet = row[1][:300] if row[1] else ""
                content = (
                    f"标题: {title}\n"
                    f"来源: {source_name}\n"
                    f"路径: {uri}\n"
                    f"片段: {snippet}\n"
                    f"item_id: {row[0]}"
                )
                results.append(
                    MemoryItem(
                        id=eid,
                        content=content,
                        created_at=(
                            updated_at.isoformat()
                            if updated_at and hasattr(updated_at, "isoformat")
                            else (indexed_at.isoformat() if indexed_at and hasattr(indexed_at, "isoformat") else None)
                        ),
                        categories=[f"external:{source_name}"],
                    )
                )
    except Exception as exc:
        logger.warning("external LIKE search failed", error=str(exc))
    return results


async def _search_provider(query: str, limit: int, seen: set[str]) -> list[MemoryItem]:
    """DocumentIndexProvider 语义搜索（LanceDB/HRR）。"""
    results: list[MemoryItem] = []
    try:
        from backend.modules.data_sources.ingestion import get_document_index_provider
        from backend.modules.data_sources.ingestion.providers.null import NullProvider

        provider = get_document_index_provider()
        if provider is None or isinstance(provider, NullProvider):
            return results

        text_result = await provider.prefetch(query)
        if not text_result:
            return results

        # 解析 Provider 返回格式: [来源: doc_id]\ncontent
        for match in _PROVIDER_RESULT_RE.finditer(text_result):
            source_info = match.group(1).strip()
            content = match.group(2).strip()
            if not content or content in seen:
                continue
            seen.add(content)

            # 提取 doc_id（格式可能是 "doc_id (相似度: 0.85)"）
            doc_id = source_info.split("(")[0].strip()
            results.append(
                MemoryItem(
                    id=f"provider:{doc_id}",
                    content=content[:500],
                    categories=[f"provider:{provider.name}"],
                )
            )
            if len(results) >= limit:
                break
    except Exception as exc:
        logger.warning("Provider search failed", error=str(exc))
    return results
