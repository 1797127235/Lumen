"""SQLite 数据库迁移脚本

由 main.py lifespan 调用，幂等执行 DDL（加列、索引、FTS5、触发器）。
所有原始 SQL 集中在此，main.py 只做应用组装。
"""

from __future__ import annotations

from sqlalchemy import text

from shared.logging import get_logger

logger = get_logger(__name__)

# ── GrowthEvent FTS5 DDL（供 migrations.py 和 relational_store.py 共享）──

_GROWTH_EVENTS_FTS_DDL: list[str] = [
    # FTS5 主表
    """CREATE VIRTUAL TABLE IF NOT EXISTS growth_events_fts USING fts5(
        event_type, entity_type, entity_id, payload_json
    )""",
    # Trigram 表（CJK 子串搜索）
    """CREATE VIRTUAL TABLE IF NOT EXISTS growth_events_fts_trigram USING fts5(
        event_type, entity_type, entity_id, payload_json,
        tokenize='trigram'
    )""",
    # 回填已有数据
    """INSERT INTO growth_events_fts(rowid, event_type, entity_type, entity_id, payload_json)
        SELECT rowid, event_type, entity_type, entity_id, COALESCE(payload_json, '') FROM growth_events
        WHERE rowid NOT IN (SELECT rowid FROM growth_events_fts)""",
    """INSERT INTO growth_events_fts_trigram(rowid, event_type, entity_type, entity_id, payload_json)
        SELECT rowid, event_type, entity_type, entity_id, COALESCE(payload_json, '') FROM growth_events
        WHERE rowid NOT IN (SELECT rowid FROM growth_events_fts_trigram)""",
    # AFTER INSERT 触发器
    """CREATE TRIGGER IF NOT EXISTS trg_growth_events_ai AFTER INSERT ON growth_events BEGIN
        INSERT INTO growth_events_fts(rowid, event_type, entity_type, entity_id, payload_json)
        VALUES (new.rowid, new.event_type, new.entity_type, new.entity_id, new.payload_json);
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_growth_events_tri_ai AFTER INSERT ON growth_events BEGIN
        INSERT INTO growth_events_fts_trigram(rowid, event_type, entity_type, entity_id, payload_json)
        VALUES (new.rowid, new.event_type, new.entity_type, new.entity_id, new.payload_json);
    END""",
    # AFTER DELETE 触发器
    # 注意：FTS5 的 'delete' 特殊插入语法在触发器中会报 SQL logic error，
    # 改用直接 DELETE FROM（SQLite 3.45+ 的 FTS5 已支持）。
    """CREATE TRIGGER IF NOT EXISTS trg_growth_events_ad AFTER DELETE ON growth_events BEGIN
        DELETE FROM growth_events_fts WHERE rowid = old.rowid;
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_growth_events_tri_ad AFTER DELETE ON growth_events BEGIN
        DELETE FROM growth_events_fts_trigram WHERE rowid = old.rowid;
    END""",
    # AFTER UPDATE 触发器
    """CREATE TRIGGER IF NOT EXISTS trg_growth_events_au AFTER UPDATE ON growth_events BEGIN
        DELETE FROM growth_events_fts WHERE rowid = old.rowid;
        INSERT INTO growth_events_fts(rowid, event_type, entity_type, entity_id, payload_json)
        VALUES (new.rowid, new.event_type, new.entity_type, new.entity_id, new.payload_json);
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_growth_events_tri_au AFTER UPDATE ON growth_events BEGIN
        DELETE FROM growth_events_fts_trigram WHERE rowid = old.rowid;
        INSERT INTO growth_events_fts_trigram(rowid, event_type, entity_type, entity_id, payload_json)
        VALUES (new.rowid, new.event_type, new.entity_type, new.entity_id, new.payload_json);
    END""",
]

_GROWTH_EVENTS_FTS_TRIGGER_NAMES = [
    "trg_growth_events_ai",
    "trg_growth_events_tri_ai",
    "trg_growth_events_ad",
    "trg_growth_events_tri_ad",
    "trg_growth_events_au",
    "trg_growth_events_tri_au",
]


async def rebuild_growth_events_fts(conn) -> None:
    """全量重建 GrowthEvent FTS5 索引（DROP + CREATE + 回填）。

    用于批量删除等场景，调用方负责事务管理。
    """
    # 1. 移除触发器
    for name in _GROWTH_EVENTS_FTS_TRIGGER_NAMES:
        await conn.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
    # 2. 移除旧 FTS 表
    await conn.execute(text("DROP TABLE IF EXISTS growth_events_fts"))
    await conn.execute(text("DROP TABLE IF EXISTS growth_events_fts_trigram"))
    # 3. 重建
    for sql in _GROWTH_EVENTS_FTS_DDL:
        await conn.execute(text(sql))
    logger.info("GrowthEvent FTS index rebuilt")


async def migrate_sqlite(conn) -> None:
    """幂等加列：create_all 不 ALTER 已有表，SQLite 需手动补列。"""
    for sql in [
        "DROP TABLE IF EXISTS jd_diagnoses",
        "ALTER TABLE conversations ADD COLUMN summary TEXT",
        "ALTER TABLE conversations ADD COLUMN pydantic_messages TEXT",
        "ALTER TABLE growth_events ADD COLUMN dedupe_key VARCHAR(128)",
        "ALTER TABLE growth_events ADD COLUMN payload_hash VARCHAR(64)",
        "ALTER TABLE growth_events ADD COLUMN projected_md_at DATETIME",
        "ALTER TABLE growth_events ADD COLUMN projected_cognee_at DATETIME",
        "ALTER TABLE growth_events ADD COLUMN projected_provider_at DATETIME",
        # 迁移旧数据：将 projected_cognee_at 的值复制到 projected_provider_at
        "UPDATE growth_events SET projected_provider_at = projected_cognee_at WHERE projected_provider_at IS NULL AND projected_cognee_at IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_user_event ON growth_events (user_id, event_type)",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_user_entity ON growth_events (user_id, entity_type, entity_id)",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_dedupe ON growth_events (user_id, dedupe_key)",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_unprojected_md ON growth_events (user_id, projected_md_at)",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_unprojected_provider ON growth_events (user_id, projected_provider_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_events_user_dedupe ON growth_events (user_id, dedupe_key)",
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
        # ── Workstream B: 语义去重字段 ──
        "ALTER TABLE growth_events ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'active'",
        "ALTER TABLE growth_events ADD COLUMN updated_at DATETIME",
        "ALTER TABLE growth_events ADD COLUMN merged_from TEXT",
        "ALTER TABLE growth_events ADD COLUMN original_dedupe_key VARCHAR(128)",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_status ON growth_events (user_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_original_dedupe ON growth_events (original_dedupe_key)",
        # ── 记忆审核机制 ──
        "ALTER TABLE growth_events ADD COLUMN confirmation_status VARCHAR(16) NOT NULL DEFAULT 'confirmed'",
        "ALTER TABLE growth_events ADD COLUMN reviewed_at DATETIME",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_confirmation ON growth_events (user_id, confirmation_status)",
        # ── Lumen 伴侣系统 ──
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
        """CREATE TABLE IF NOT EXISTS lumen_presence (
            user_id TEXT NOT NULL DEFAULT 'demo_user',
            last_user_at DATETIME,
            last_proactive_at DATETIME,
            proactive_sent_24h INTEGER DEFAULT 0,
            followup_due_at DATETIME,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id)
        )""",
        "INSERT OR IGNORE INTO lumen_presence (user_id) VALUES ('demo_user')",
    ]:
        try:
            await conn.execute(text(sql))
        except Exception as e:
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                logger.warning("SQLite 迁移失败", sql=sql[:100], error=str(e))

    # GrowthEvent FTS5 表和触发器（共享 DDL，与 relational_store.py 共用）
    for sql in _GROWTH_EVENTS_FTS_DDL:
        try:
            await conn.execute(text(sql))
        except Exception as e:
            if "already exists" not in str(e).lower():
                logger.warning("FTS DDL 失败", sql=sql[:100], error=str(e))
