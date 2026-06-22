"""订阅信息流应用 — SQLite 存储 (aiosqlite)。

表：
- feeds      订阅源
- items      条目（带 analyzed 标记）
- analysis   AI 分析结果
- ack_state  已读状态
- focus_cache 单行表，缓存 Lumen push 进来的关注点（真相在 Lumen USER.md）
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feeds (
    id TEXT PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    last_fetched_at TEXT NOT NULL DEFAULT '',
    etag TEXT NOT NULL DEFAULT '',
    last_modified TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    feed_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL,
    analyzed INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (feed_id) REFERENCES feeds(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_items_feed ON items(feed_id);
CREATE INDEX IF NOT EXISTS idx_items_fetched ON items(fetched_at);
CREATE INDEX IF NOT EXISTS idx_items_unanalyzed ON items(analyzed, fetched_at);
CREATE TABLE IF NOT EXISTS analysis (
    item_id TEXT PRIMARY KEY,
    relevance TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL DEFAULT '',
    focus_snapshot TEXT NOT NULL DEFAULT '[]',
    analyzed_at TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS ack_state (
    item_id TEXT PRIMARY KEY,
    acked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS focus_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    focus_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT ''
);
INSERT OR IGNORE INTO focus_cache (id, focus_json, updated_at) VALUES (1, '[]', '');
"""


class FeedStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "store 未 init"
        return self._db

    # ── feeds ──────────────────────────────────────────
    async def list_feeds(self) -> list[dict[str, Any]]:
        cur = await self.db.execute("SELECT * FROM feeds ORDER BY added_at")
        return [dict(r) for r in await cur.fetchall()]

    async def get_feed_by_url(self, url: str) -> dict[str, Any] | None:
        cur = await self.db.execute("SELECT * FROM feeds WHERE url = ?", (url,))
        r = await cur.fetchone()
        return dict(r) if r else None

    async def upsert_feed(self, feed: dict[str, Any]) -> None:
        await self.db.execute(
            """INSERT INTO feeds (id, url, title, link, description, added_at, last_fetched_at, etag, last_modified)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                 title=excluded.title, link=excluded.link, description=excluded.description""",
            (
                feed["id"],
                feed["url"],
                feed.get("title", ""),
                feed.get("link", ""),
                feed.get("description", ""),
                feed.get("added_at", ""),
                feed.get("last_fetched_at", ""),
                feed.get("etag", ""),
                feed.get("last_modified", ""),
            ),
        )
        await self.db.commit()

    async def update_feed_fetched(self, feed_id: str, etag: str, last_modified: str) -> None:
        await self.db.execute(
            "UPDATE feeds SET last_fetched_at = ?, etag = ?, last_modified = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), etag, last_modified, feed_id),
        )
        await self.db.commit()

    async def delete_feed(self, feed_id: str) -> None:
        await self.db.execute(
            "DELETE FROM analysis WHERE item_id IN (SELECT id FROM items WHERE feed_id = ?)", (feed_id,)
        )
        await self.db.execute("DELETE FROM items WHERE feed_id = ?", (feed_id,))
        await self.db.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        await self.db.commit()

    # ── items ──────────────────────────────────────────
    async def insert_item(self, item: dict[str, Any]) -> bool:
        """返回 True=新增，False=已存在。"""
        cur = await self.db.execute(
            """INSERT OR IGNORE INTO items (id, feed_id, title, link, summary, published_at, fetched_at, analyzed)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                item["id"],
                item["feed_id"],
                item.get("title", ""),
                item.get("link", ""),
                item.get("summary", ""),
                item.get("published_at", ""),
                item.get("fetched_at", ""),
            ),
        )
        await self.db.commit()
        return cur.rowcount > 0

    async def get_unanalyzed_items(self, limit: int) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT * FROM items WHERE analyzed = 0 ORDER BY fetched_at LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def mark_analyzed(self, item_ids: list[str]) -> None:
        if not item_ids:
            return
        await self.db.executemany(
            "UPDATE items SET analyzed = 1 WHERE id = ?",
            [(iid,) for iid in item_ids],
        )
        await self.db.commit()

    async def get_unread_items(self, limit: int) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            """SELECT i.id, i.feed_id, i.title, i.link, i.summary, i.published_at, i.fetched_at,
                      a.relevance, a.summary AS analysis_summary, a.verdict
               FROM items i
               LEFT JOIN analysis a ON a.item_id = i.id
               WHERE i.id NOT IN (SELECT item_id FROM ack_state)
               ORDER BY COALESCE(NULLIF(i.published_at, ''), i.fetched_at) DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_item(self, item_id: str) -> dict[str, Any] | None:
        cur = await self.db.execute("SELECT * FROM items WHERE id = ?", (item_id,))
        r = await cur.fetchone()
        return dict(r) if r else None

    # ── analysis ───────────────────────────────────────
    async def upsert_analysis(
        self,
        item_id: str,
        relevance: str,
        summary: str,
        verdict: str,
        focus_snapshot: list[str],
    ) -> None:
        await self.db.execute(
            """INSERT INTO analysis (item_id, relevance, summary, verdict, focus_snapshot, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(item_id) DO UPDATE SET
                 relevance=excluded.relevance, summary=excluded.summary,
                 verdict=excluded.verdict, focus_snapshot=excluded.focus_snapshot,
                 analyzed_at=excluded.analyzed_at""",
            (
                item_id,
                relevance,
                summary,
                verdict,
                json.dumps(focus_snapshot, ensure_ascii=False),
                datetime.now(UTC).isoformat(),
            ),
        )
        await self.db.commit()

    async def get_analysis(self, item_id: str) -> dict[str, Any] | None:
        cur = await self.db.execute("SELECT * FROM analysis WHERE item_id = ?", (item_id,))
        r = await cur.fetchone()
        return dict(r) if r else None

    # ── ack ────────────────────────────────────────────
    async def ack_items(self, item_ids: list[str]) -> int:
        if not item_ids:
            return 0
        now = datetime.now(UTC).isoformat()
        await self.db.executemany(
            "INSERT OR IGNORE INTO ack_state (item_id, acked_at) VALUES (?, ?)",
            [(iid, now) for iid in item_ids],
        )
        await self.db.commit()
        return len(item_ids)

    # ── focus（缓存 Lumen push 的关注点）──────────────
    async def set_focus(self, focus: list[str]) -> None:
        await self.db.execute(
            "UPDATE focus_cache SET focus_json = ?, updated_at = ? WHERE id = 1",
            (json.dumps(focus, ensure_ascii=False), datetime.now(UTC).isoformat()),
        )
        await self.db.commit()

    async def get_focus(self) -> list[str]:
        cur = await self.db.execute("SELECT focus_json FROM focus_cache WHERE id = 1")
        r = await cur.fetchone()
        if not r:
            return []
        try:
            return json.loads(r["focus_json"])
        except Exception:
            return []
