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
) -> list[MemoryItem]:
    """搜索 Narrative 事件记忆。

    FTS5（全文）→ Cognee（语义，可选 Phase 2）。

    include_cognee: 默认 False — Cognee 留作 Phase 2 外部数据接入。
    datasets=None 时 Cognee 搜全部 dataset。
    """
    seen: set[str] = set()
    results: list[MemoryItem] = []

    if include_cognee:
        results.extend(await _search_cognee(query, limit, seen, datasets=datasets))
    results.extend(await _search_fts5(user_id, query, limit, seen))

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


def _escape_fts5(query: str) -> str:
    """转义 FTS5 MATCH 查询中的特殊字符。

    FTS5 MATCH 将 + - * / ( ) " 等视为操作符/保留字符。
    用双引号包裹整个查询可将其视为字面量词组，同时转义内部的双引号。
    """
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


async def _search_fts5(user_id: str, query: str, limit: int, seen: set[str]) -> list[MemoryItem]:
    """SQLite FTS5 全文搜索 — 仅搜索 Narrative 事件。"""
    results: list[MemoryItem] = []
    try:
        fts_table = "growth_events_fts_trigram" if _CJK_RE.search(query) else "growth_events_fts"
        safe_query = _escape_fts5(query)
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
