"""CodePilot 后端入口"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.backend.config import get_settings
from app.backend.db.base import Base, get_engine, init_db
from app.backend.models import *  # noqa — 确保所有模型注册到 Base
from app.backend.routers import chat, health, jd, profile, targets

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库表 + SQLite 兼容迁移"""
    init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLite: create_all 不 ALTER 已有表，手动加列
        if "sqlite" in str(engine.url):
            await _migrate_sqlite(conn)
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


settings = get_settings()

app = FastAPI(
    title="CodePilot · 码路领航",
    description="面向计算机学生的AI职业规划智能体",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"] if settings.debug else ["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"] if settings.debug else ["Authorization", "Content-Type"],
)

app.include_router(health.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(jd.router, prefix="/api")
app.include_router(targets.router, prefix="/api")
