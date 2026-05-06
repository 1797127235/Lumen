"""CareerOS 记忆层统一门面。

所有记忆读写经此类收敛，调用方不碰后端编排。

Write:  memory.remember(user_id, event_type, ..., *, db=None)
        memory.remember_batch(user_id, events, *, db=None)
        memory.flush_projections(user_id, event_ids)
Read:   memory.recall(user_id, query, limit=10)
        memory.build_context(user_id, user_input=None)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TypedDict

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.backend.db.base import get_async_session_maker
from app.backend.models.growth_event import GrowthEvent
from app.backend.services.growth_event_service import create_growth_event_with_dedup

logger = logging.getLogger(__name__)


# ── 公共类型 ──


class EventSpec(TypedDict, total=False):
    event_type: str
    entity_type: str | None
    entity_id: str | None
    payload: dict | None
    source: str


class MemoryItem(BaseModel):
    id: str
    content: str
    created_at: str | None = None
    categories: list[str] = Field(default_factory=list)


# ── 门面 ──


class CareerOSMemory:
    """记忆层统一门面 — 单例，无状态。"""

    # ── 写入 ──────────────────────────────────────────────
    async def remember(
        self,
        user_id: str,
        event_type: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        payload: dict | None = None,
        source: str = "system",
        *,
        db: AsyncSession | None = None,
    ) -> GrowthEvent | None:
        """写入一条记忆事件。
        db=None:  自开 session，commit + 同步 .md + async Cognee。
        db=外部:  在该 session 内 write + .md 投影。调用方管理事务。
        """
        if db is not None:
            from app.backend.services.md_projector import sync_user_md_projection

            event = await create_growth_event_with_dedup(
                db=db,
                user_id=user_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload,
                source=source,
            )
            if event:
                await db.commit()
                await sync_user_md_projection(user_id)
                asyncio.create_task(self._sync_cognee([str(event.id)]))  # noqa: RUF006
            return event

        async with get_async_session_maker()() as db:
            event = await create_growth_event_with_dedup(
                db=db,
                user_id=user_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload,
                source=source,
            )
            if event:
                await db.commit()
                await self.flush_projections(user_id, [str(event.id)])
            return event

    async def remember_batch(
        self,
        user_id: str,
        events: list[EventSpec],
        *,
        db: AsyncSession | None = None,
    ) -> list[GrowthEvent]:
        """批量写入，单事务。

        db=None: 自开 session，一次 commit + 同步全部投影。
        """
        if db is not None:
            created: list[GrowthEvent] = []
            for spec in events:
                event = await create_growth_event_with_dedup(
                    db=db,
                    user_id=user_id,
                    event_type=spec["event_type"],
                    entity_type=spec.get("entity_type"),
                    entity_id=spec.get("entity_id"),
                    payload=spec.get("payload"),
                    source=spec.get("source", "system"),
                )
                if event:
                    created.append(event)
            return created

        async with get_async_session_maker()() as db:
            created: list[GrowthEvent] = []
            for spec in events:
                event = await create_growth_event_with_dedup(
                    db=db,
                    user_id=user_id,
                    event_type=spec["event_type"],
                    entity_type=spec.get("entity_type"),
                    entity_id=spec.get("entity_id"),
                    payload=spec.get("payload"),
                    source=spec.get("source", "system"),
                )
                if event:
                    created.append(event)
            if created:
                await db.commit()
                await self.flush_projections(user_id, [str(e.id) for e in created])
            return created

    async def flush_projections(
        self,
        user_id: str,
        event_ids: list[str] | None = None,
    ) -> None:
        """同步 .md 文件 + 异步投 Cognee。

        Agent 工具路径：已通过外部 db 写入 SQLite 并 commit 后调用。
        """
        from app.backend.services.md_projector import sync_user_md_projection

        await sync_user_md_projection(user_id)
        if event_ids:
            asyncio.create_task(self._sync_cognee(event_ids))  # noqa: RUF006 fire-and-forget

    async def _sync_cognee(self, event_ids: list[str]) -> None:
        try:
            from app.backend.services.cognee_projector import project_event_ids

            await project_event_ids(event_ids)
        except Exception as exc:
            logger.warning("Cognee projection skipped: count=%d, error=%s", len(event_ids), exc)

    # ── 读取 ──────────────────────────────────────────────

    async def recall(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
    ) -> list[MemoryItem]:
        """语义搜索 + 子串 fallback，按 event_id 去重。

        Cognee 语义优先 → SQLite LIKE → .md 子串兜底。
        """
        from app.backend.services import cognee_service
        from app.backend.services.memory_service import search_memory

        seen: set[str] = set()
        results: list[MemoryItem] = []

        # 1. Cognee 语义搜索（带 event_id）
        try:
            cognee_items = await cognee_service.recall(user_id, query, limit=limit)
            for item in cognee_items:
                eid = item.get("event_id") or ""
                content = (item.get("text") or "").strip()
                if not content:
                    continue
                if eid and eid in seen:
                    continue
                if eid:
                    seen.add(eid)
                results.append(
                    MemoryItem(
                        id=eid or f"cognee:{hash(content)}",
                        content=content[:500],
                        created_at=item.get("created_at"),
                        categories=[item.get("event_type", "")] if item.get("event_type") else [],
                    )
                )
        except Exception as exc:
            logger.warning("Cognee recall in facade failed: %s", exc)

        # 2. SQLite LIKE fallback
        try:
            from sqlalchemy import or_, select

            from app.backend.models.growth_event import GrowthEvent

            async with get_async_session_maker()() as db:
                stmt = (
                    select(GrowthEvent)
                    .where(
                        GrowthEvent.user_id == user_id,
                        or_(
                            GrowthEvent.payload_json.contains(query),
                            GrowthEvent.event_type.contains(query),
                        ),
                    )
                    .order_by(GrowthEvent.created_at.desc())
                    .limit(limit)
                )
                rows = (await db.execute(stmt)).scalars().all()
                for row in rows:
                    eid = str(row.id)
                    if eid in seen:
                        continue
                    seen.add(eid)
                    results.append(
                        MemoryItem(
                            id=eid,
                            content=row.payload_json or f"{row.event_type}: {row.entity_type or ''}",
                            created_at=row.created_at.isoformat() if row.created_at else None,
                            categories=[row.event_type] if row.event_type else [],
                        )
                    )
        except Exception as exc:
            logger.warning("SQLite recall fallback failed: %s", exc)

        # 3. .md 子串兜底
        try:
            md_items = search_memory(user_id, query)
            for item in md_items:
                file_id = f"md:{item['file']}"
                if file_id in seen:
                    continue
                seen.add(file_id)
                results.append(
                    MemoryItem(
                        id=file_id,
                        content=item["content"][:500],
                        created_at=None,
                        categories=[item["section"]],
                    )
                )
        except Exception as exc:
            logger.warning(".md recall fallback failed: %s", exc)

        return results[:limit]

    async def build_context(
        self,
        user_id: str,
        user_input: str | None = None,
    ) -> str:
        """构建 system prompt 记忆上下文。

        1. 结构化画像（全量 .md files）
        2. 如果提供 user_input，附加 Cognee 语义相关片段
        """
        from app.backend.services.memory_limits import EXPERIENCES_CHAR_LIMIT, MEMORY_CHAR_LIMIT, SKILLS_CHAR_LIMIT
        from app.backend.services.memory_service import read_experiences, read_memory, read_skills

        parts: list[str] = []

        _limits = {
            "memory": MEMORY_CHAR_LIMIT,
            "skills": SKILLS_CHAR_LIMIT,
            "experiences": EXPERIENCES_CHAR_LIMIT,
        }

        def _block(label: str, name: str, content: str) -> str:
            chars = len(content)
            limit = _limits.get(name, 0)
            pct = int(chars / limit * 100) if limit else 0
            header = f"══ {label} [{pct}% — {chars:,}/{limit:,} 字符] ══"
            return f"{header}\n{content.strip()}"

        # 结构化画像
        for label, name, reader in [
            ("核心记忆", "memory", read_memory),
            ("技能", "skills", read_skills),
            ("经历", "experiences", read_experiences),
        ]:
            try:
                content = reader(user_id)
                if content and content.strip():
                    parts.append(_block(label, name, content))
            except Exception:
                pass

        # 语义相关片段
        if user_input:
            try:
                items = await self.recall(user_id, user_input, limit=5)
                if items:
                    lines = ["【相关记忆（语义检索）】"]
                    for item in items:
                        lines.append(f"- {item.content[:300]}")
                    parts.append("\n".join(lines))
            except Exception:
                pass

        return "\n\n".join(parts) if parts else ""


# ── 模块级单例 ──

_memory: CareerOSMemory | None = None


def get_memory() -> CareerOSMemory:
    global _memory
    if _memory is None:
        _memory = CareerOSMemory()
    return _memory
