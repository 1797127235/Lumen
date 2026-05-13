"""统一搜索层 — 从多个存储源召回记忆。

主路径：FTS5 全文搜索（Narrative 事件 only）
可选路径：Cognee 语义搜索（Phase 2 外部数据用）

Profile 事件不走搜索索引 — L0 固定注入已覆盖。"""

from __future__ import annotations

import re as _re

from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.db import get_async_session_maker
from backend.logging_config import get_logger
from backend.memory.classifier import NARRATIVE_EVENT_TYPES
from backend.memory.datasets import ALL_DATASETS

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
    *,
    datasets: list[str] | None = None,
    include_cognee: bool = False,
    source_scope: str = "narrative",  # "narrative" | "external" | "all"
) -> list[MemoryItem]:
    """搜索 Narrative 事件记忆。

    FTS5（全文）→ Cognee（语义，可选 Phase 2）。

    include_cognee: 默认 False — Cognee 留作 Phase 2 外部数据接入。
    datasets=None 时 Cognee 搜全部 dataset。
    source_scope: 控制搜索范围 — narrative（默认，仅事件）/ external（仅外部文档）/ all（两者）
    """
    seen: set[str] = set()
    results: list[MemoryItem] = []

    if include_cognee and source_scope in ("narrative", "all"):
        results.extend(await _search_cognee(query, limit, seen, datasets=datasets))

    if source_scope in ("narrative", "all"):
        results.extend(await _search_fts5(user_id, query, limit, seen))

    if source_scope in ("external", "all"):
        results.extend(await _search_external_fts5(query, limit, seen))

    return results[:limit]


async def _search_cognee(
    query: str,
    limit: int,
    seen: set[str],
    *,
    datasets: list[str] | None = None,
) -> list[MemoryItem]:
    """Cognee 语义搜索。datasets=None 时搜全部 dataset。"""
    results: list[MemoryItem] = []
    try:
        from backend.memory.semantic_store import SemanticStore

        store = SemanticStore()
        search_datasets = datasets if datasets is not None else ALL_DATASETS
        texts = await store.search(query, datasets=search_datasets, top_k=limit)
        for text_content in texts:
            content = text_content.strip()
            if not content or content in seen:
                continue
            seen.add(content)
            results.append(
                MemoryItem(
                    id=f"cognee:{hash(content)}",
                    content=content[:500],
                )
            )
    except Exception as exc:
        logger.warning("Cognee search skipped", error=str(exc))
    return results


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


async def _search_external_fts5(query: str, limit: int, seen: set[str]) -> list[MemoryItem]:
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
            return await _search_external_like(query, limit, seen)

        fts_table = "external_items_fts_trigram" if is_cjk else "external_items_fts"
        async with get_async_session_maker()() as db:
            rows = (
                await db.execute(
                    text(f"""
                        SELECT ei.id, ei.content, ei.source_id, ei.doc_id, ei.indexed_at
                        FROM external_items ei
                        JOIN {fts_table} fts ON fts.rowid = ei.rowid
                        WHERE {fts_table} MATCH :q
                        ORDER BY ei.indexed_at DESC
                        LIMIT :lim
                    """),
                    {"q": safe_query, "lim": limit},
                )
            ).all()

            for row in rows:
                eid = f"ext:{row[0]}"
                if eid in seen:
                    continue
                seen.add(eid)
                created_at = row[4]
                results.append(
                    MemoryItem(
                        id=eid,
                        content=row[1][:500] if row[1] else "",
                        created_at=created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
                        categories=[f"external:{row[2]}"],
                    )
                )
    except Exception as exc:
        logger.warning("external_fts5 search failed", error=str(exc))
    return results


def _count_cjk_chars(query: str) -> int:
    """统计查询中的 CJK 字符数。"""
    return sum(1 for ch in query if _CJK_RE.match(ch))


async def _search_external_like(query: str, limit: int, seen: set[str]) -> list[MemoryItem]:
    """LIKE fallback — CJK 短查询（1-2 字）时 trigram 无法命中。"""
    results: list[MemoryItem] = []
    # 安全转义 LIKE 通配符（用 ! 作为 ESCAPE 字符，避免反斜杠跨层转义问题）
    escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    like_pattern = "%" + escaped + "%"
    try:
        async with get_async_session_maker()() as db:
            rows = (
                await db.execute(
                    text("""
                        SELECT ei.id, ei.content, ei.source_id, ei.doc_id, ei.indexed_at
                        FROM external_items ei
                        WHERE ei.content LIKE :q ESCAPE '!'
                        ORDER BY ei.indexed_at DESC
                        LIMIT :lim
                    """),
                    {"q": like_pattern, "lim": limit},
                )
            ).all()

            for row in rows:
                eid = f"ext:{row[0]}"
                if eid in seen:
                    continue
                seen.add(eid)
                created_at = row[4]
                results.append(
                    MemoryItem(
                        id=eid,
                        content=row[1][:500] if row[1] else "",
                        created_at=created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
                        categories=[f"external:{row[2]}"],
                    )
                )
    except Exception as exc:
        logger.warning("external LIKE search failed", error=str(exc))
    return results
