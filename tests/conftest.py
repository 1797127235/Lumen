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


class _MockProvider:
    """内存中的 DocumentIndexProvider 模拟，用于测试 Provider 搜索集成。"""

    name = "mock"

    def __init__(self):
        self._docs: dict[str, str] = {}

    async def initialize(self) -> None:
        pass

    async def prefetch(self, query: str) -> list:
        from backend.modules.data_sources.ingestion.document_index_provider import ProviderHit

        q = query.lower()
        hits = []
        for doc_id, content in self._docs.items():
            if any(word in content.lower() for word in q.split()):
                hits.append(ProviderHit(doc_id=doc_id, content=content))
        return hits

    async def sync_document(self, content: str, doc_id: str, metadata=None) -> None:
        self._docs[doc_id] = content


@pytest.fixture
async def mock_provider():
    """创建一个 mock DocumentIndexProvider 并注入到全局 Pipeline。"""
    import tempfile
    from pathlib import Path

    from backend.modules.data_sources.ingestion.pipeline import init_pipeline

    with tempfile.TemporaryDirectory() as tmpdir:
        provider = _MockProvider()
        await provider.initialize()
        # 索引一些测试文档
        await provider.sync_document(
            "Machine learning is a subset of artificial intelligence. "
            "It involves training models on data to make predictions.",
            "doc1",
        )
        await provider.sync_document(
            "Python is a popular programming language for data science. It has libraries like pandas and numpy.",
            "doc2",
        )

        # 注入到全局 Pipeline
        init_pipeline(Path(tmpdir), document_index_provider=provider)
        yield provider
