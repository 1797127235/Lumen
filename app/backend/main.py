"""CareerOS 后端入口"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.backend.config import apply_user_config, get_settings
from app.backend.db.base import Base, get_engine, init_db
from app.backend.models import *  # noqa — 确保所有模型注册到 Base
from app.backend.routers import chat, config_router, health, memory, profile

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库表 + SQLite 兼容迁移 + 加载用户配置"""
    settings = get_settings()
    init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if "sqlite" in str(engine.url):
            await _migrate_sqlite(conn)
    applied = apply_user_config(settings)
    if applied:
        logger.info("config.json 覆盖: %s", list(applied.keys()))
    # Cognee 记忆层初始化（可选，失败不阻塞启动）
    from app.backend.agent.cognee_client import init_cognee

    cognee_status = init_cognee()
    logger.info("Cognee 状态: %s", cognee_status)
    yield
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
    ]:
        try:
            await conn.execute(text(sql))
        except Exception as e:
            if "duplicate column" not in str(e).lower() and "already exists" not in str(e).lower():
                logger.warning("SQLite 迁移失败: %s — %s", sql, e)


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

# ── API 路由 ──

app.include_router(health.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(config_router.router, prefix="/api")

# ── 生产模式：托管前端静态文件 + SPA 路由 ──

if not _settings.debug:
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
