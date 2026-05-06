"""CareerOS 后端入口"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.backend.config import apply_user_config, get_settings
from app.backend.db.base import Base, get_engine, init_db
from app.backend.logging_config import RequestLoggingMiddleware, get_logger, setup_logging
from app.backend.models import *  # noqa — 确保所有模型注册到 Base
from app.backend.routers import chat, config_router, health, memory

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库表 + SQLite 兼容迁移 + 加载用户配置"""
    settings = get_settings()

    # 初始化日志系统（生产环境 JSON，开发环境彩色控制台）
    setup_logging(json_logs=not settings.debug, log_level="DEBUG" if settings.debug else "INFO")

    init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if "sqlite" in str(engine.url):
            await _migrate_sqlite(conn)
    applied = apply_user_config(settings)
    if applied:
        logger.info("config.json 覆盖", keys=list(applied.keys()))
    # Cognee 记忆层初始化（后台线程，不阻塞启动）
    import threading

    from app.backend.agent.cognee_client import init_cognee

    threading.Thread(target=init_cognee, daemon=True, name="cognee-init").start()

    yield
    # 关闭时取消未完成的 Cognee 投影任务
    from app.backend.services.careeros_memory import cancel_background_tasks

    cancel_background_tasks()
    await engine.dispose()


async def _migrate_sqlite(conn):
    """幂等加列：create_all 不 ALTER 已有表，SQLite 需手动补列。"""
    for sql in [
        "DROP TABLE IF EXISTS jd_diagnoses",
        "ALTER TABLE conversations ADD COLUMN summary TEXT",
        "ALTER TABLE growth_events ADD COLUMN dedupe_key VARCHAR(128)",
        "ALTER TABLE growth_events ADD COLUMN payload_hash VARCHAR(64)",
        "ALTER TABLE growth_events ADD COLUMN projected_md_at DATETIME",
        "ALTER TABLE growth_events ADD COLUMN projected_cognee_at DATETIME",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_user_event ON growth_events (user_id, event_type)",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_user_entity ON growth_events (user_id, entity_type, entity_id)",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_dedupe ON growth_events (user_id, dedupe_key)",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_unprojected_md ON growth_events (user_id, projected_md_at)",
        "CREATE INDEX IF NOT EXISTS ix_growth_events_unprojected_cognee ON growth_events (user_id, projected_cognee_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_events_user_dedupe ON growth_events (user_id, dedupe_key)",
        # FTS5 全文索引（独立表，非 external content，避免 UUID rowid 不兼容）
        """CREATE VIRTUAL TABLE IF NOT EXISTS growth_events_fts USING fts5(
            event_type, entity_type, entity_id, payload_json
        )""",
        # FTS5 同步触发器
        """CREATE TRIGGER IF NOT EXISTS trg_growth_events_ai AFTER INSERT ON growth_events BEGIN
            INSERT INTO growth_events_fts(rowid, event_type, entity_type, entity_id, payload_json)
            VALUES (new.rowid, new.event_type, new.entity_type, new.entity_id, new.payload_json);
        END""",
        """CREATE TRIGGER IF NOT EXISTS trg_growth_events_ad AFTER DELETE ON growth_events BEGIN
            INSERT INTO growth_events_fts(growth_events_fts, rowid, event_type, entity_type, entity_id, payload_json)
            VALUES ('delete', old.rowid, old.event_type, old.entity_type, old.entity_id, old.payload_json);
        END""",
        """CREATE TRIGGER IF NOT EXISTS trg_growth_events_au AFTER UPDATE ON growth_events BEGIN
            INSERT INTO growth_events_fts(growth_events_fts, rowid, event_type, entity_type, entity_id, payload_json)
            VALUES ('delete', old.rowid, old.event_type, old.entity_type, old.entity_id, old.payload_json);
            INSERT INTO growth_events_fts(rowid, event_type, entity_type, entity_id, payload_json)
            VALUES (new.rowid, new.event_type, new.entity_type, new.entity_id, new.payload_json);
        END""",
        # 回填已有数据
        """INSERT INTO growth_events_fts(rowid, event_type, entity_type, entity_id, payload_json)
            SELECT rowid, event_type, entity_type, entity_id, payload_json FROM growth_events
            WHERE rowid NOT IN (SELECT rowid FROM growth_events_fts)""",
        # Trigram FTS5：CJK 子串搜索（3 字节重叠，中文/日文/韩文友好）
        """CREATE VIRTUAL TABLE IF NOT EXISTS growth_events_fts_trigram USING fts5(
            event_type, entity_type, entity_id, payload_json,
            tokenize='trigram'
        )""",
        """CREATE TRIGGER IF NOT EXISTS trg_growth_events_tri_ai AFTER INSERT ON growth_events BEGIN
            INSERT INTO growth_events_fts_trigram(rowid, event_type, entity_type, entity_id, payload_json)
            VALUES (new.rowid, new.event_type, new.entity_type, new.entity_id, new.payload_json);
        END""",
        """CREATE TRIGGER IF NOT EXISTS trg_growth_events_tri_ad AFTER DELETE ON growth_events BEGIN
            INSERT INTO growth_events_fts_trigram(growth_events_fts_trigram, rowid, event_type, entity_type, entity_id, payload_json)
            VALUES ('delete', old.rowid, old.event_type, old.entity_type, old.entity_id, old.payload_json);
        END""",
        """CREATE TRIGGER IF NOT EXISTS trg_growth_events_tri_au AFTER UPDATE ON growth_events BEGIN
            INSERT INTO growth_events_fts_trigram(growth_events_fts_trigram, rowid, event_type, entity_type, entity_id, payload_json)
            VALUES ('delete', old.rowid, old.event_type, old.entity_type, old.entity_id, old.payload_json);
            INSERT INTO growth_events_fts_trigram(rowid, event_type, entity_type, entity_id, payload_json)
            VALUES (new.rowid, new.event_type, new.entity_type, new.entity_id, new.payload_json);
        END""",
        """INSERT INTO growth_events_fts_trigram(rowid, event_type, entity_type, entity_id, payload_json)
            SELECT rowid, event_type, entity_type, entity_id, payload_json FROM growth_events
            WHERE rowid NOT IN (SELECT rowid FROM growth_events_fts_trigram)""",
    ]:
        try:
            await conn.execute(text(sql))
        except Exception as e:
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                logger.warning("SQLite 迁移失败", sql=sql[:100], error=str(e))


app = FastAPI(
    title="CareerOS",
    description="从大一陪伴到毕业的 AI 职业规划助手",
    version="0.2.0",
    lifespan=lifespan,
)

# ── CORS：开发模式允许跨域（前端 :5173），生产模式不需（单端口）──

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"] if _settings.debug else ["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"] if _settings.debug else ["Authorization", "Content-Type"],
)

# ── 请求日志中间件 ──
app.add_middleware(RequestLoggingMiddleware)

# ── API 路由 ──

app.include_router(health.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(config_router.router, prefix="/api")

# ── 静态文件托管：dist/ 存在时始终挂载（开发/桌面/生产都可用） ──
if True:  # 始终启用（桌面/生产模式依赖此挂载）
    from pathlib import Path

    static_dir = Path(__file__).parent.parent / "frontend" / "dist"
    if static_dir.exists():
        # API 404 兜底：避免未匹配的 /api/* 被静态文件拦截
        @app.get("/api/{path:path}")
        async def api_not_found(path: str):
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Not Found"}, status_code=404)

        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
