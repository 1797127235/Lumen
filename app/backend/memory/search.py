"""统一搜索层 — 从多个存储源召回记忆。

优先级：Cognee 语义 → SQLite FTS5 全文 → .md 子串兜底

从 lumen_memory.recall() 提取。"""

from __future__ import annotations

import re as _re

from pydantic import BaseModel, Field
from sqlalchemy import text

from app.backend.db.base import get_async_session_maker
from app.backend.logging_config import get_logger
from app.backend.memory.cognee_admin.datasets import ALL_DATASETS
from app.backend.memory.projections.markdown import read_experiences, read_memory, read_skills

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
    include_cognee: bool = True,
) -> list[MemoryItem]:
    """多源搜索记忆。

    Cognee（语义）→ FTS5（全文）→ .md（子串兜底），三选二去重。
    datasets=None 时 Cognee 搜全部 dataset。
    """
    seen: set[str] = set()
    results: list[MemoryItem] = []

    if include_cognee:
        results.extend(await _search_cognee(query, limit, seen, datasets=datasets))
    results.extend(await _search_fts5(user_id, query, limit, seen))
    results.extend(await _search_md(user_id, query, seen))

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
        from app.backend.memory.stores.semantic import SemanticStore

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


async def _search_fts5(user_id: str, query: str, limit: int, seen: set[str]) -> list[MemoryItem]:
    """SQLite FTS5 全文搜索。"""
    results: list[MemoryItem] = []
    try:
        fts_table = "growth_events_fts_trigram" if _CJK_RE.search(query) else "growth_events_fts"
        async with get_async_session_maker()() as db:
            rows = (
                await db.execute(
                    _fts_query(fts_table),
                    {"uid": user_id, "q": query, "lim": limit},
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
        WHERE ge.user_id = :uid AND {table_name} MATCH :q
        ORDER BY ge.created_at DESC
        LIMIT :lim
    """)


async def _search_md(user_id: str, query: str, seen: set[str]) -> list[MemoryItem]:
    """.md 文件子串搜索（兜底）。"""
    results: list[MemoryItem] = []
    try:
        for md_name, md_reader in [
            ("memory.md", read_memory),
            ("skills.md", read_skills),
            ("experiences.md", read_experiences),
        ]:
            content = md_reader(user_id)
            if query.lower() not in content.lower():
                continue
            file_id = f"md:{md_name}"
            if file_id in seen:
                continue
            seen.add(file_id)
            lines = content.split("\n")
            relevant: list[str] = []
            for idx, line in enumerate(lines):
                if query.lower() in line.lower():
                    start = max(0, idx - 2)
                    end = min(len(lines), idx + 3)
                    relevant.extend(lines[start:end])
                    relevant.append("---")
            unique = list(dict.fromkeys(relevant))
            results.append(
                MemoryItem(
                    id=file_id,
                    content="\n".join(unique)[:500],
                    categories=[md_name.replace(".md", "")],
                )
            )
    except Exception as exc:
        logger.warning(".md search fallback failed", error=str(exc))
    return results
