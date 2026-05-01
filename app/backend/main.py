"""CodePilot 后端入口"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.backend.config import get_settings
from app.backend.db.base import Base, init_db, get_engine
from app.backend.models import *  # noqa — 确保所有模型注册到 Base
from app.backend.routers import health, chat, profile


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库表"""
    init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


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
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
