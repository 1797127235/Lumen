"""测试配置 — 隔离的 SQLite 内存数据库 + TestClient"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.db import Base, get_async_session_maker, get_db, init_db
from backend.main import app

# ── 测试用内存数据库 ─────────────────────────────────────
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    """测试会话开始时建表，结束时清理"""
    init_db(TEST_DATABASE_URL)
    from backend.core.db import get_engine
    from backend.core.migrations import migrate_sqlite

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await migrate_sqlite(conn)
    yield
    await engine.dispose()


@pytest.fixture
async def db(setup_db) -> AsyncGenerator[AsyncSession, None]:
    """每个测试用独立 session，测试后回滚"""
    session_maker = get_async_session_maker()
    async with session_maker() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client(setup_db) -> AsyncGenerator[AsyncClient, None]:
    """异步 HTTP 测试客户端，覆盖 DB 依赖"""

    async def _override_get_db():
        session_maker = get_async_session_maker()
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # 先应用依赖覆盖
    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
