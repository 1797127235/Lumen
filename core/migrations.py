"""SQLite 数据库迁移脚本

由 main.py lifespan 调用，幂等执行 DDL（加列、索引、FTS5、触发器）。
所有原始 SQL 集中在此，main.py 只做应用组装。
"""

from __future__ import annotations

from sqlalchemy import text

from shared.logging import get_logger

logger = get_logger(__name__)


async def migrate_sqlite(conn) -> None:
    """幂等加列：create_all 不 ALTER 已有表，SQLite 需手动补列。"""
    for sql in [
        "DROP TABLE IF EXISTS jd_diagnoses",
        # ── Hermes-Pure 清理：退役旧记忆事件表 ──
        "DROP TABLE IF EXISTS growth_events",
        "DROP TABLE IF EXISTS growth_events_fts",
        "ALTER TABLE conversations ADD COLUMN summary TEXT",
        "ALTER TABLE conversations ADD COLUMN pydantic_messages TEXT",
        # ── data_sources: 用户数据源连接（Phase 2b）──
        """CREATE TABLE IF NOT EXISTS data_sources (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'demo_user',
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            config_json TEXT NOT NULL DEFAULT '{}',
            credential_ref TEXT,
            capabilities_json TEXT NOT NULL DEFAULT '[]',
            last_sync_at TIMESTAMP,
            last_error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS ix_data_sources_user ON data_sources (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_data_sources_user_status ON data_sources (user_id, status)",
        # ── external_items: 外部数据文档索引（Phase 2a/2b）──
        """CREATE TABLE IF NOT EXISTS external_items (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'demo_user',
            data_source_id TEXT,
            connector_type TEXT,
            source_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            external_id TEXT,
            uri TEXT,
            title TEXT,
            content TEXT,
            content_hash TEXT,
            metadata_json TEXT,
            indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            deleted_at TIMESTAMP,
            UNIQUE(source_id, doc_id)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_external_items_source_doc ON external_items (source_id, doc_id)",
        "CREATE INDEX IF NOT EXISTS ix_external_items_data_source ON external_items (data_source_id)",
        "CREATE INDEX IF NOT EXISTS ix_external_items_user ON external_items (user_id)",
        # 幂等加列（SQLite ALTER TABLE ADD COLUMN）
        "ALTER TABLE external_items ADD COLUMN user_id TEXT NOT NULL DEFAULT 'demo_user'",
        "ALTER TABLE external_items ADD COLUMN data_source_id TEXT",
        "ALTER TABLE external_items ADD COLUMN connector_type TEXT",
        "ALTER TABLE external_items ADD COLUMN external_id TEXT",
        "ALTER TABLE external_items ADD COLUMN uri TEXT",
        "ALTER TABLE external_items ADD COLUMN title TEXT",
        "ALTER TABLE external_items ADD COLUMN updated_at TIMESTAMP",
        "ALTER TABLE external_items ADD COLUMN deleted_at TIMESTAMP",
        # 回填已有数据
        "UPDATE external_items SET data_source_id = source_id, connector_type = source_id, external_id = doc_id WHERE data_source_id IS NULL",
        # 新增唯一约束索引（data_source_id + external_id）
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_external_items_ds_ext ON external_items (data_source_id, external_id)",
        """CREATE VIRTUAL TABLE IF NOT EXISTS external_items_fts USING fts5(
            content
        )""",
        """CREATE TRIGGER IF NOT EXISTS trg_external_items_ai AFTER INSERT ON external_items BEGIN
            INSERT INTO external_items_fts(rowid, content)
            VALUES (new.rowid, new.content);
        END""",
        """CREATE TRIGGER IF NOT EXISTS trg_external_items_ad AFTER DELETE ON external_items BEGIN
            DELETE FROM external_items_fts WHERE rowid = old.rowid;
        END""",
        """CREATE TRIGGER IF NOT EXISTS trg_external_items_au AFTER UPDATE ON external_items BEGIN
            DELETE FROM external_items_fts WHERE rowid = old.rowid;
            INSERT INTO external_items_fts(rowid, content)
            VALUES (new.rowid, new.content);
        END""",
        """INSERT INTO external_items_fts(rowid, content)
            SELECT rowid, content FROM external_items
            WHERE rowid NOT IN (SELECT rowid FROM external_items_fts)""",
        """CREATE VIRTUAL TABLE IF NOT EXISTS external_items_fts_trigram USING fts5(
            content,
            tokenize='trigram'
        )""",
        """CREATE TRIGGER IF NOT EXISTS trg_external_items_tri_ai AFTER INSERT ON external_items BEGIN
            INSERT INTO external_items_fts_trigram(rowid, content)
            VALUES (new.rowid, new.content);
        END""",
        """CREATE TRIGGER IF NOT EXISTS trg_external_items_tri_ad AFTER DELETE ON external_items BEGIN
            DELETE FROM external_items_fts_trigram WHERE rowid = old.rowid;
        END""",
        """CREATE TRIGGER IF NOT EXISTS trg_external_items_tri_au AFTER UPDATE ON external_items BEGIN
            DELETE FROM external_items_fts_trigram WHERE rowid = old.rowid;
            INSERT INTO external_items_fts_trigram(rowid, content)
            VALUES (new.rowid, new.content);
        END""",
        """INSERT INTO external_items_fts_trigram(rowid, content)
            SELECT rowid, content FROM external_items
            WHERE rowid NOT IN (SELECT rowid FROM external_items_fts_trigram)""",
        # ── ingestion_state: 摄入状态追踪（替代 JSON IngestionStore）──
        """CREATE TABLE IF NOT EXISTS ingestion_state (
            data_source_id TEXT NOT NULL,
            external_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'indexed',
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,
            indexed_at TIMESTAMP,
            PRIMARY KEY (data_source_id, external_id)
        )""",
        "CREATE INDEX IF NOT EXISTS ix_ingestion_state_ds ON ingestion_state (data_source_id)",
        "CREATE INDEX IF NOT EXISTS ix_ingestion_state_status ON ingestion_state (status)",
        # ── Lumen 伙伴系统 ──
        """CREATE TABLE IF NOT EXISTS lumen_thoughts (
            id INTEGER PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'demo_user',
            content TEXT NOT NULL,
            source_event_ids TEXT,
            judge_score REAL,
            judge_veto TEXT,
            duplicate INTEGER DEFAULT 0,
            mood TEXT CHECK(mood IN ('calm','curious','tender','reflective','energized')),
            sent_at DATETIME,
            error_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS ix_lumen_thoughts_user ON lumen_thoughts (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_lumen_thoughts_sent ON lumen_thoughts (user_id, sent_at)",
        """CREATE TABLE IF NOT EXISTS lumen_state (
            user_id TEXT NOT NULL DEFAULT 'demo_user',
            mood TEXT NOT NULL DEFAULT 'calm'
                CHECK(mood IN ('calm','curious','tender','reflective','energized')),
            mood_intensity REAL DEFAULT 0.5,
            pending_mood TEXT
                CHECK(pending_mood IS NULL OR pending_mood IN ('calm','curious','tender','reflective','energized')),
            pending_count INTEGER DEFAULT 0,
            derived_from TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id)
        )""",
        "INSERT OR IGNORE INTO lumen_state (user_id, mood) VALUES ('demo_user', 'calm')",
    ]:
        try:
            await conn.execute(text(sql))
        except Exception as e:
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                logger.warning("SQLite 迁移失败", sql=sql[:100], error=str(e))

    # 移除已退役的 lumen_presence 表（原 RSS/主动推送门控使用）
    try:
        await conn.execute(text("DROP TABLE IF EXISTS lumen_presence"))
    except Exception as e:
        logger.warning("清理 lumen_presence 表失败", error=str(e))


async def migrate_md_files() -> None:
    """Hermes-Pure: 旧文件命名迁移 — memory.md → MEMORY.md, about_you.md → USER.md。"""

    from core.config import USER_DATA_DIR

    base_dir = USER_DATA_DIR / "memory"
    if not base_dir.exists():
        return

    for user_dir in base_dir.iterdir():
        if not user_dir.is_dir():
            continue

        old_memory = user_dir / "memory.md"
        new_memory = user_dir / "MEMORY.md"
        if old_memory.exists() and not new_memory.exists():
            try:
                old_memory.rename(new_memory)
                logger.info("迁移 memory.md → MEMORY.md", user=user_dir.name)
            except OSError as e:
                logger.warning("memory.md 迁移失败", user=user_dir.name, error=str(e))

        old_about = user_dir / "about_you.md"
        new_about = user_dir / "USER.md"
        if old_about.exists() and not new_about.exists():
            try:
                old_about.rename(new_about)
                logger.info("迁移 about_you.md → USER.md", user=user_dir.name)
            except OSError as e:
                logger.warning("about_you.md 迁移失败", user=user_dir.name, error=str(e))
