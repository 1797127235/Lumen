"""SQLite 连接与初始化。"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_db: aiosqlite.Connection | None = None


async def get_db(db_path: str | None = None) -> aiosqlite.Connection:
    """获取数据库连接（单例）。

    Args:
        db_path: 数据库文件路径，默认 ~/.lumen/knowledge.db
    """
    global _db

    if _db is not None:
        return _db

    if db_path is None:
        db_path = str(Path.home() / ".lumen" / "knowledge.db")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row

    await _db.execute("PRAGMA journal_mode = WAL")
    await _db.execute("PRAGMA foreign_keys = ON")

    await run_migrations(_db)

    logger.info("Knowledge DB 已连接: %s", db_path)
    return _db


async def close_db() -> None:
    """关闭数据库连接。"""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


MIGRATIONS = [
    {
        "version": 1,
        "sql": """
            CREATE TABLE IF NOT EXISTS kb_documents (
                id           TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                path         TEXT,
                checksum     TEXT,
                chunks_count INTEGER DEFAULT 0,
                status       TEXT NOT NULL DEFAULT 'processing',
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS kb_chunks (
                id            TEXT PRIMARY KEY,
                document_id   TEXT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
                text          TEXT NOT NULL,
                embedding     BLOB,
                chunk_index   INTEGER NOT NULL,
                token_count   INTEGER,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_doc ON kb_chunks(document_id);
        """,
    }
]


async def run_migrations(db: aiosqlite.Connection) -> None:
    """运行数据库迁移。"""
    await db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
    await db.commit()

    cursor = await db.execute("SELECT MAX(version) AS v FROM schema_version")
    row = await cursor.fetchone()
    current = row["v"] if row and row["v"] else 0

    for m in MIGRATIONS:
        if m["version"] <= current:
            continue
        try:
            await db.executescript(m["sql"])
            await db.execute("INSERT INTO schema_version (version) VALUES (?)", (m["version"],))
            await db.commit()
            logger.info("Migration applied: version=%d", m["version"])
        except Exception:
            await db.rollback()
            raise
