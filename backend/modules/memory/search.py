"""统一搜索层 — 从多个存储源召回记忆。

三条搜索管线并行运行，结果合并去重：
  - Provider 语义搜索（Cognee/LanceDB/HRR）— 覆盖 narrative 事件 + 外部文档
  - FTS5 关键词搜索（growth_events_fts）— Narrative 事件全文检索
  - FTS5 关键词搜索（external_items_fts）— 外部文档全文检索

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
) -> list[MemoryItem]:
    """统一搜索：Provider（语义）+ FTS5（关键词），三路并行合并去重。

    不再区分数据来源 — 搜索全部数据，靠 categories 字段标识来源。
    Provider 不可用时自动降级（NullProvider 返回空列表）。
    """
    seen: set[str] = set()
    results: list[MemoryItem] = []

    # Provider 语义搜索 — 覆盖 narrative 事件 + 外部文档
    provider_results = await _search_provider(query, limit)
    for item in provider_results:
        if item.id not in seen:
            seen.add(item.id)
            results.append(item)

    # FTS5 关键词 — narrative 事件
    fts5_results = await _search_fts5(user_id, query, limit, seen)
    for item in fts5_results:
        if item.id not in seen:
            seen.add(item.id)
            results.append(item)

    # FTS5 关键词 — 外部文档
    ext_results = await _search_external_fts5(query, limit, seen, user_id)
    for item in ext_results:
        if item.id not in seen:
            seen.add(item.id)
            results.append(item)

    return results[:limit]


_FTS5_SPECIAL_RE = _re.compile(r'[+\-*"()^@]')


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
    except Exception:
        logger.exception("FTS5 search failed", user_id=user_id, query=query)
    return results


def _fts_query(table_name: str):
    from sqlalchemy import bindparam

    return text(f"""
        SELECT ge.id, ge.payload_json, ge.event_type, ge.entity_type, ge.created_at
        FROM growth_events ge
        JOIN {table_name} fts ON fts.rowid = ge.rowid
        WHERE ge.user_id = :uid
          AND ge.event_type IN :etypes
          AND {table_name} MATCH :q
        ORDER BY ge.created_at DESC
        LIMIT :lim
    """).bindparams(bindparam("etypes", expanding=True))


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
                content = f"标题: {title}\n来源: {source_name}\n路径: {uri}\n片段: {snippet}\nitem_id: {row[0]}"
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
    except Exception:
        logger.exception("external_fts5 search failed", query=query)
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
                content = f"标题: {title}\n来源: {source_name}\n路径: {uri}\n片段: {snippet}\nitem_id: {row[0]}"
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
    except Exception:
        logger.exception("external LIKE search failed", query=query)
    return results


async def _search_provider(query: str, limit: int) -> list[MemoryItem]:
    """DocumentIndexProvider 语义搜索（LanceDB/HRR/Cognee）。

    返回的 item.id 格式：
      - narrative 事件: 裸 event UUID（与 _search_fts5 一致，自然去重）
      - 外部文档: f"provider:{doc_id}"（不与外部 FTS5 的 ext: 格式碰撞）
    """
    results: list[MemoryItem] = []
    try:
        from backend.modules.data_sources.ingestion import get_document_index_provider
        from backend.modules.data_sources.ingestion.providers.null import NullProvider

        provider = get_document_index_provider()
        if provider is None or isinstance(provider, NullProvider):
            return results

        hits = await provider.prefetch(query)
        for hit in hits:
            if len(results) >= limit:
                break

            # narrative 事件: doc_id = "narrative:{event_uuid}" → 裸 UUID
            if hit.doc_id.startswith("narrative:"):
                item_id = hit.doc_id[10:]
                categories = [f"provider:{provider.name}"]
            else:
                # 外部文档: 保持 provider: 前缀，与 ext: 格式区分
                item_id = f"provider:{hit.doc_id}"
                categories = [f"provider:{provider.name}"]

            results.append(
                MemoryItem(
                    id=item_id,
                    content=hit.content,
                    categories=categories,
                )
            )
    except Exception:
        logger.exception("Provider search failed", query=query)
    return results
