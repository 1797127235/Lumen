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
from app.backend.routers import chat, config_router, health, jd, profile, targets

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
    # 记忆索引：首次启动时为用户构建向量索引
    if settings.dashscope_api_key:
        from app.backend.agent.rag import ingest_user_memory
        from app.backend.db.session import get_async_session_maker

        async with get_async_session_maker()() as session:
            doc_count = await ingest_user_memory(session, user_id="demo_user")
            if doc_count:
                logger.info("记忆索引完成：%d 条文档", doc_count)
    yield
    await engine.dispose()


async def _migrate_sqlite(conn):
    """幂等加列：create_all 不 ALTER 已有表，SQLite 需手动补列。"""
    for sql in [
        "ALTER TABLE conversations ADD COLUMN summary TEXT",
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
app.include_router(chat.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(jd.router, prefix="/api")
app.include_router(targets.router, prefix="/api")
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
