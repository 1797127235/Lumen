"""统一搜索层 — 从多个存储源召回记忆。

三条搜索管线并行运行，结果合并去重：
  - Provider 语义搜索（LanceDB）— 覆盖 narrative 事件 + 外部文档
  - FTS5 关键词搜索（growth_events_fts）— Narrative 事件全文检索
  - FTS5 关键词搜索（external_items_fts）— 外部文档全文检索

Profile 事件不走搜索索引 — L0 固定注入已覆盖。"""

from __future__ import annotations

import asyncio
import re as _re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text

from core.db import get_async_session_maker
from lib.memory.classifier import NARRATIVE_EVENT_TYPES, PROFILE_EVENT_TYPES
from lib.memory.models import GrowthEvent
from shared.logging import get_logger

logger = get_logger(__name__)

_CJK_RE = _re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


class MemoryItem(BaseModel):
    """搜索结果条目。"""

    id: str
    content: str
    created_at: str | None = None
    categories: list[str] = Field(default_factory=list)


async def _filter_rejected_narrative(user_id: str, items: list[MemoryItem]) -> list[MemoryItem]:
    """过滤掉 confirmation_status='rejected' 的 narrative 事件。"""
    narrative_ids = [item.id for item in items if not item.id.startswith(("ext:", "provider:"))]
    if not narrative_ids:
        return items
    async with get_async_session_maker()() as db:
        from sqlalchemy import select

        result = await db.execute(
            select(GrowthEvent.id).where(
                GrowthEvent.user_id == user_id,
                GrowthEvent.id.in_(narrative_ids),
                GrowthEvent.confirmation_status == "rejected",
            )
        )
        rejected_ids = {row[0] for row in result.all()}
    return [item for item in items if item.id not in rejected_ids]


async def search_all(
    user_id: str,
    query: str,
    limit: int = 10,
    time_start: datetime | None = None,
    time_end: datetime | None = None,
) -> list[MemoryItem]:
    """统一搜索：Provider（语义）+ FTS5（关键词），三路并行合并去重。

    不再区分数据来源 — 搜索全部数据，靠 categories 字段标识来源。
    Provider 不可用时自动降级（NullProvider 返回空列表）。

    time_start/time_end: 可选时间范围过滤（UTC datetime）。
    """
    # 三路并行搜索，各数据源独立召回，最后统一去重
    provider_results, fts5_results, ext_results = await asyncio.gather(
        _search_provider(query, limit, time_start=time_start, time_end=time_end),
        _search_fts5(user_id, query, limit, time_start=time_start, time_end=time_end),
        _search_external_fts5(query, limit, user_id, time_start=time_start, time_end=time_end),
    )

    seen: set[str] = set()
    results: list[MemoryItem] = []

    for item in provider_results:
        if item.id not in seen:
            seen.add(item.id)
            results.append(item)

    for item in fts5_results:
        if item.id not in seen:
            seen.add(item.id)
            results.append(item)

    for item in ext_results:
        if item.id not in seen:
            seen.add(item.id)
            results.append(item)

    # 过滤 rejected 的 narrative 事件
    results = await _filter_rejected_narrative(user_id, results)
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


async def _search_fts5(
    user_id: str,
    query: str,
    limit: int,
    time_start: datetime | None = None,
    time_end: datetime | None = None,
) -> list[MemoryItem]:
    """SQLite FTS5 全文搜索 — 覆盖所有事件类型（含 Profile）。

    CJK 查询如果 MATCH 返回空，自动 fallback 到 jieba 分词 + LIKE，
    解决 trigram tokenizer 对短词（<3 字）和长句的盲区。
    """
    results: list[MemoryItem] = []
    safe_query = _escape_fts5(query)
    if not safe_query:
        return results
    try:
        fts_table = "growth_events_fts_trigram" if _CJK_RE.search(query) else "growth_events_fts"
        async with get_async_session_maker()() as db:
            params = {
                "uid": user_id,
                "etypes": tuple(NARRATIVE_EVENT_TYPES | PROFILE_EVENT_TYPES),
                "q": safe_query,
                "lim": limit,
            }
            if time_start:
                params["time_start"] = time_start
            if time_end:
                params["time_end"] = time_end

            rows = (
                await db.execute(
                    _fts_query(fts_table, time_start=time_start, time_end=time_end),
                    params,
                )
            ).all()

            for row in rows:
                eid = str(row[0])
                created_at = row[4]
                if created_at is not None:
                    if isinstance(created_at, datetime):
                        created_at = created_at.isoformat()
                    elif isinstance(created_at, str):
                        pass  # 已经是 ISO 格式字符串
                    else:
                        created_at = str(created_at)
                results.append(
                    MemoryItem(
                        id=eid,
                        content=row[1] or f"{row[2]}: {row[3] or ''}",
                        created_at=created_at,
                        categories=[row[2]] if row[2] else [],
                    )
                )
    except Exception:
        logger.exception("FTS5 search failed", user_id=user_id, query=query)

    # CJK fallback: MATCH 为空时用 jieba 分词 + LIKE 兜底
    if not results and _CJK_RE.search(query):
        try:
            results = await _search_cjk_like(user_id, query, limit, time_start, time_end)
        except Exception:
            logger.exception("CJK LIKE fallback failed", user_id=user_id, query=query)

    return results


async def _search_cjk_like(
    user_id: str,
    query: str,
    limit: int,
    time_start: datetime | None = None,
    time_end: datetime | None = None,
) -> list[MemoryItem]:
    """CJK 搜索 fallback：jieba 分词提取关键词，用 LIKE 匹配 payload_json。

    解决 trigram tokenizer 对 <3 字词和长句的盲区。
    数据量小时（<1000 条）LIKE 扫描性能完全可接受。
    """
    import jieba
    from sqlalchemy import select

    # jieba 分词，取长度 >= 2 且包含 CJK 的关键词
    keywords: list[str] = []
    seen_kw: set[str] = set()
    for w in jieba.lcut(query):
        w = w.strip()
        if len(w) >= 2 and _CJK_RE.search(w) and w not in seen_kw:
            seen_kw.add(w)
            keywords.append(w)

    # 没提取到有效关键词时，用原始 query 整体 LIKE
    if not keywords:
        raw = query.strip()
        if len(raw) >= 2:
            keywords = [raw]
        else:
            return []

    async with get_async_session_maker()() as db:
        seen_ids: set[str] = set()
        all_results: list[MemoryItem] = []

        for kw in keywords:
            if len(all_results) >= limit:
                break

            pattern = f"%{kw}%"
            stmt = select(
                GrowthEvent.id,
                GrowthEvent.payload_json,
                GrowthEvent.event_type,
                GrowthEvent.entity_type,
                GrowthEvent.created_at,
            ).where(
                GrowthEvent.user_id == user_id,
                GrowthEvent.confirmation_status != "rejected",
                GrowthEvent.payload_json.like(pattern),
            )
            if time_start:
                stmt = stmt.where(GrowthEvent.created_at >= time_start)
            if time_end:
                stmt = stmt.where(GrowthEvent.created_at < time_end)
            stmt = stmt.order_by(GrowthEvent.created_at.desc()).limit(limit)

            result = await db.execute(stmt)
            for row in result.all():
                eid = str(row[0])
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    created_at = row[4]
                    if created_at is not None:
                        if isinstance(created_at, datetime):
                            created_at = created_at.isoformat()
                        elif isinstance(created_at, str):
                            pass
                        else:
                            created_at = str(created_at)
                    all_results.append(
                        MemoryItem(
                            id=eid,
                            content=row[1] or f"{row[2]}: {row[3] or ''}",
                            created_at=created_at,
                            categories=[row[2]] if row[2] else [],
                        )
                    )

        return all_results[:limit]


def _fts_query(table_name: str, time_start: datetime | None = None, time_end: datetime | None = None):
    from sqlalchemy import bindparam

    time_clauses = []
    if time_start:
        time_clauses.append("ge.created_at >= :time_start")
    if time_end:
        time_clauses.append("ge.created_at < :time_end")

    time_where = ""
    if time_clauses:
        time_where = " AND " + " AND ".join(time_clauses)

    return text(f"""
        SELECT ge.id, ge.payload_json, ge.event_type, ge.entity_type, ge.created_at
        FROM growth_events ge
        JOIN {table_name} fts ON fts.rowid = ge.rowid
        WHERE ge.user_id = :uid
          AND ge.event_type IN :etypes
          AND ge.confirmation_status != 'rejected'
          AND {table_name} MATCH :q
          {time_where}
        ORDER BY ge.created_at DESC
        LIMIT :lim
    """).bindparams(bindparam("etypes", expanding=True))


async def _search_external_fts5(
    query: str,
    limit: int,
    user_id: str | None = None,
    time_start: datetime | None = None,
    time_end: datetime | None = None,
) -> list[MemoryItem]:
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
            return await _search_external_like(query, limit, user_id)

        fts_table = "external_items_fts_trigram" if is_cjk else "external_items_fts"
        params: dict[str, Any] = {"q": safe_query, "lim": limit}
        user_filter = ""
        if user_id:
            user_filter = "AND ei.user_id = :uid AND (ds.status = 'active' OR ds.id IS NULL)"
            params["uid"] = user_id

        time_filter = ""
        if time_start:
            time_filter += " AND ei.indexed_at >= :time_start"
            params["time_start"] = time_start
        if time_end:
            time_filter += " AND ei.indexed_at < :time_end"
            params["time_end"] = time_end

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
                          {time_filter}
                        ORDER BY ei.indexed_at DESC
                        LIMIT :lim
                    """),
                    params,
                )
            ).all()

            for row in rows:
                eid = f"ext:{row[0]}"
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


async def _search_external_like(
    query: str,
    limit: int,
    user_id: str | None = None,
    time_start: datetime | None = None,
    time_end: datetime | None = None,
) -> list[MemoryItem]:
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

    time_filter = ""
    if time_start:
        time_filter += " AND ei.indexed_at >= :time_start"
        params["time_start"] = time_start
    if time_end:
        time_filter += " AND ei.indexed_at < :time_end"
        params["time_end"] = time_end

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
                          {time_filter}
                        ORDER BY ei.indexed_at DESC
                        LIMIT :lim
                    """),
                    params,
                )
            ).all()

            for row in rows:
                eid = f"ext:{row[0]}"
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


def _parse_iso_datetime(dt_str: str | None) -> datetime | None:
    """解析 ISO 格式日期时间字符串为 datetime 对象。"""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def _search_provider(
    query: str,
    limit: int,
    time_start: datetime | None = None,
    time_end: datetime | None = None,
) -> list[MemoryItem]:
    """DocumentIndexProvider 语义搜索（LanceDB）。

    返回的 item.id 格式：
      - narrative 事件: 裸 event UUID（与 _search_fts5 一致，自然去重）
      - 外部文档: f"provider:{doc_id}"（不与外部 FTS5 的 ext: 格式碰撞）
    """
    results: list[MemoryItem] = []
    try:
        from core.vector_store import NullProvider, get_document_index_provider

        provider = get_document_index_provider()
        if provider is None or isinstance(provider, NullProvider):
            return results

        hits = await provider.prefetch(query)
        for hit in hits:
            if len(results) >= limit:
                break

            # 时间过滤：解析 metadata 中的 created_at
            if time_start or time_end:
                metadata = hit.metadata if hasattr(hit, "metadata") else {}
                hit_dt = _parse_iso_datetime(metadata.get("created_at") if isinstance(metadata, dict) else None)
                if hit_dt:
                    if time_start and hit_dt < time_start:
                        continue
                    if time_end and hit_dt >= time_end:
                        continue

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
