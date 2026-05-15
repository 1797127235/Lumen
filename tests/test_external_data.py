"""外部数据接入测试 — ingestion、FTS5 搜索、CJK 命中、删除处理。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.core.db import get_async_session_maker, get_engine, init_db
from backend.core.migrations import migrate_sqlite
from backend.modules.data_sources.ingestion import init_pipeline
from backend.modules.data_sources.ingestion.connectors.local_folder import FilesystemConnector
from backend.modules.data_sources.ingestion.store import IngestionStore
from backend.modules.memory.search import _search_external_fts5, _search_external_like


@pytest.fixture
async def migrated_db():
    """提供已执行过 migrate_sqlite 的内存数据库 session。"""
    init_db("sqlite+aiosqlite:///:memory:")
    engine = get_engine()
    from backend.core.db import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await migrate_sqlite(conn)

    session_maker = get_async_session_maker()
    async with session_maker() as session:
        yield session
        await session.rollback()

    await engine.dispose()


# ── IngestionStore ──


async def test_ingestion_store_dedup(migrated_db):
    """内容未变时不应重复索引。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = IngestionStore(Path(tmpdir) / "state.json")
        doc_id = "/notes/hello.md"
        content_hash = "abc123"

        assert not await store.is_indexed(doc_id, content_hash)
        await store.mark_indexed(doc_id, content_hash, "filesystem")
        assert await store.is_indexed(doc_id, content_hash)
        # hash 变了 → 视为未索引
        assert not await store.is_indexed(doc_id, "def456")


async def test_ingestion_store_failed_retry(migrated_db):
    """失败记录应累加重试次数。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = IngestionStore(Path(tmpdir) / "state.json")
        doc_id = "/notes/fail.md"

        assert await store.get_retry_count(doc_id) == 0
        await store.mark_failed(doc_id, "DB timeout")
        assert await store.get_retry_count(doc_id) == 1
        await store.mark_failed(doc_id, "DB timeout again")
        assert await store.get_retry_count(doc_id) == 2
        # 成功后失败记录应被清除
        await store.mark_indexed(doc_id, "hash", "filesystem")
        assert await store.get_retry_count(doc_id) == 0


# ── FilesystemConnector ──


@pytest.fixture
def sample_vault(tmp_path: Path) -> Path:
    """创建一个模拟 Obsidian vault，含正常文件和隐藏目录。"""
    vault = tmp_path / "vault"
    vault.mkdir()

    # 正常文件
    (vault / "hello.md").write_text("Hello world", encoding="utf-8")
    (vault / "笔记.md").write_text("这是一篇中文笔记", encoding="utf-8")
    (vault / "big.txt").write_text("x" * 100_000, encoding="utf-8")

    # 隐藏目录（应被跳过）
    hidden = vault / ".obsidian"
    hidden.mkdir()
    (hidden / "config.md").write_text("should be skipped", encoding="utf-8")

    # 子目录
    sub = vault / "projects"
    sub.mkdir()
    (sub / "proj.md").write_text("Project notes", encoding="utf-8")

    return vault


async def test_filesystem_connector_scan(sample_vault: Path) -> None:
    """扫描应发现所有非隐藏目录下的支持文件。"""
    conn = FilesystemConnector([str(sample_vault)])
    docs = []
    async for doc in conn.scan():
        docs.append(doc)

    doc_ids = {d.external_id for d in docs}
    assert any("hello.md" in d for d in doc_ids)
    assert any("笔记.md" in d for d in doc_ids)
    assert any("proj.md" in d for d in doc_ids)
    # 隐藏目录应被跳过
    assert not any(".obsidian" in d for d in doc_ids)


async def test_filesystem_connector_no_truncation(sample_vault: Path) -> None:
    """Phase 2: Connector 不做截断，返回完整原始字节。"""
    conn = FilesystemConnector([str(sample_vault)])
    async for raw in conn.scan():
        if "big.txt" in raw.external_id:
            assert len(raw.content_bytes) == 100_000  # 完整读取
            break
    else:
        pytest.fail("big.txt not found")


# ── Pipeline + DB 搜索 ──


async def test_external_items_fts_search(migrated_db) -> None:
    """写入 external_items 后，FTS5 应能搜到英文内容。"""
    async with get_async_session_maker()() as db:
        await db.execute(
            text(
                """
                INSERT INTO external_items (id, source_id, doc_id, content, content_hash, metadata_json)
                VALUES (:id, :sid, :did, :content, :hash, :meta)
            """
            ),
            {
                "id": "fs:test1",
                "sid": "filesystem",
                "did": "/a/b.md",
                "content": "Python async programming patterns",
                "hash": "h1",
                "meta": json.dumps({"title": "b"}),
            },
        )
        await db.commit()

    results = await _search_external_fts5("Python async", 5, set())
    assert len(results) >= 1
    assert any("Python async" in r.content for r in results)


async def test_external_items_update(migrated_db) -> None:
    """更新内容后，搜索结果应反映新内容。"""
    async with get_async_session_maker()() as db:
        await db.execute(
            text(
                """
                INSERT INTO external_items (id, source_id, doc_id, content, content_hash, metadata_json)
                VALUES (:id, :sid, :did, :content, :hash, :meta)
            """
            ),
            {
                "id": "fs:upd",
                "sid": "filesystem",
                "did": "/a/c.md",
                "content": "old content here",
                "hash": "old",
                "meta": json.dumps({}),
            },
        )
        await db.commit()

    # 先搜旧内容
    old = await _search_external_fts5("old content", 5, set())
    assert len(old) == 1

    # 更新（ON CONFLICT DO UPDATE）
    async with get_async_session_maker()() as db:
        await db.execute(
            text(
                """
                INSERT INTO external_items (id, source_id, doc_id, content, content_hash, metadata_json)
                VALUES (:id, :sid, :did, :content, :hash, :meta)
                ON CONFLICT(source_id, doc_id) DO UPDATE SET
                    content = excluded.content,
                    content_hash = excluded.content_hash,
                    indexed_at = CURRENT_TIMESTAMP
            """
            ),
            {
                "id": "fs:upd",
                "sid": "filesystem",
                "did": "/a/c.md",
                "content": "brand new updated content",
                "hash": "new",
                "meta": json.dumps({}),
            },
        )
        await db.commit()

    # 新内容可搜到
    new = await _search_external_fts5("updated content", 5, set())
    assert len(new) == 1
    assert "updated" in new[0].content

    # 旧内容不应再出现
    old_again = await _search_external_fts5("old content", 5, set())
    assert len(old_again) == 0


async def test_external_items_cjk_trigram(migrated_db) -> None:
    """3 字及以上中文查询应通过 trigram FTS5 命中。"""
    async with get_async_session_maker()() as db:
        await db.execute(
            text(
                """
                INSERT INTO external_items (id, source_id, doc_id, content, content_hash, metadata_json)
                VALUES (:id, :sid, :did, :content, :hash, :meta)
            """
            ),
            {
                "id": "fs:cjk",
                "sid": "filesystem",
                "did": "/a/中文.md",
                "content": "这是一篇关于人工智能的中文笔记",
                "hash": "hc",
                "meta": json.dumps({}),
            },
        )
        await db.commit()

    results = await _search_external_fts5("人工智能", 5, set())
    assert len(results) == 1
    assert "人工智能" in results[0].content


async def test_external_items_cjk_short_like(migrated_db) -> None:
    """1-2 字中文查询应通过 LIKE fallback 命中。"""
    async with get_async_session_maker()() as db:
        await db.execute(
            text(
                """
                INSERT INTO external_items (id, source_id, doc_id, content, content_hash, metadata_json)
                VALUES (:id, :sid, :did, :content, :hash, :meta)
            """
            ),
            {
                "id": "fs:short",
                "sid": "filesystem",
                "did": "/a/短.md",
                "content": "机器学习中的神经网络方法",
                "hash": "hs",
                "meta": json.dumps({}),
            },
        )
        await db.commit()

    # 单字
    r1 = await _search_external_fts5("网", 5, set())
    assert len(r1) == 1
    # 双字
    r2 = await _search_external_fts5("神经", 5, set())
    assert len(r2) == 1


async def test_external_search_graceful_when_empty(migrated_db) -> None:
    """无匹配时返回空列表，不抛异常。"""
    results = await _search_external_fts5("不存在的词", 5, set())
    assert results == []

    like_results = await _search_external_like("不存在", 5, set())
    assert like_results == []


async def test_pipeline_handle_delete(migrated_db) -> None:
    """handle_delete 应从 external_items 和 store 中清理记录。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        pipeline = init_pipeline(Path(tmpdir))

        # 先写入一条
        async with get_async_session_maker()() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO external_items (id, data_source_id, source_id, doc_id, external_id, content, content_hash, metadata_json)
                    VALUES (:id, :dsid, :sid, :did, :eid, :content, :hash, :meta)
                """
                ),
                {
                    "id": "fs:del",
                    "dsid": "ds_filesystem",
                    "sid": "filesystem",
                    "did": "/x/del.md",
                    "eid": "/x/del.md",
                    "content": "to be deleted",
                    "hash": "hdel",
                    "meta": json.dumps({}),
                },
            )
            await db.commit()

        # 标记为已索引
        store_key = "ds_filesystem:/x/del.md"
        await pipeline._store.mark_indexed(store_key, "hdel", "filesystem")
        assert await pipeline._store.is_indexed(store_key, "hdel")

        # 执行删除
        await pipeline.handle_delete("ds_filesystem", "/x/del.md")

        # DB 中应无记录
        async with get_async_session_maker()() as db:
            rows = (
                await db.execute(
                    text("SELECT COUNT(*) FROM external_items WHERE data_source_id = :dsid AND external_id = :eid"),
                    {"dsid": "ds_filesystem", "eid": "/x/del.md"},
                )
            ).scalar()
            assert rows == 0

        # store 中应清理
        assert not await pipeline._store.is_indexed(store_key, "hdel")


async def test_search_all_unified(migrated_db) -> None:
    """search_all 应统一搜索 narrative 事件 + 外部文档，不再区分 source_scope。"""
    from backend.modules.memory.search import search_all

    # 写入一条外部数据
    async with get_async_session_maker()() as db:
        await db.execute(
            text(
                """
                INSERT INTO external_items (id, source_id, doc_id, content, content_hash, metadata_json)
                VALUES (:id, :sid, :did, :content, :hash, :meta)
            """
            ),
            {
                "id": "fs:scope",
                "sid": "filesystem",
                "did": "/scope.md",
                "content": "scope test content",
                "hash": "hsc",
                "meta": json.dumps({}),
            },
        )
        await db.commit()

    # search_all 统一搜索全部数据
    results = await search_all("demo_user", "scope test", limit=5)
    assert len(results) >= 1
    assert any("scope" in r.content for r in results)


@pytest.mark.asyncio
async def test_search_with_provider(mock_provider) -> None:
    """search_all 应通过 Provider 召回语义搜索结果。"""
    from backend.modules.memory.search import search_all

    results = await search_all(
        "demo_user",
        "machine learning",
        limit=5,
    )

    # 应有 Provider 结果（mock_provider 已索引）
    provider_results = [r for r in results if r.id.startswith("provider:")]
    assert len(provider_results) >= 1
    assert "learning" in provider_results[0].content.lower()
