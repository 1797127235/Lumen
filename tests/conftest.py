"""测试配置 — 隔离的 SQLite 内存数据库 + TestClient"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.backend.db.base import Base
from app.backend.main import app

# ── 测试用内存数据库 ─────────────────────────────────────
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    """测试会话开始时建表，结束时清理"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture
async def db(setup_db) -> AsyncGenerator[AsyncSession, None]:
    """每个测试用独立 session，测试后回滚"""
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client(setup_db) -> AsyncGenerator[AsyncClient, None]:
    """异步 HTTP 测试客户端，覆盖 DB 依赖 + mock 后台任务"""

    async def _override_get_db():
        async with TestSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    from app.backend.db.session import get_db

    # 先应用依赖覆盖
    app.dependency_overrides[get_db] = _override_get_db

    # Mock 后台任务，避免调用 get_async_session_maker()
    with patch("app.backend.routers.targets.generate_advice", new_callable=AsyncMock):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac

    app.dependency_overrides.clear()
