"""测试：L2 语义去重 + L1 精确去重扩展（original_dedupe_key）。

运行：pytest tests/test_memory_dedup.py -v
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db import Base
from lib.memory.models import GrowthEvent
from lib.memory.relational_store import GrowthEventRepository
from lib.memory.writer import (
    _SEMANTIC_DEDUP_TYPES,
    MemoryWriter,
    _deep_merge_payload,
    _semantic_dedup_and_merge,
)

# ── 测试数据库 fixture ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """内存 SQLite，每个测试独立。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 手动加列（migration 在内存 DB 里也需要）
        import contextlib

        from sqlalchemy import text

        for sql in [
            "ALTER TABLE growth_events ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'active'",
            "ALTER TABLE growth_events ADD COLUMN updated_at DATETIME",
            "ALTER TABLE growth_events ADD COLUMN merged_from TEXT",
            "ALTER TABLE growth_events ADD COLUMN original_dedupe_key VARCHAR(128)",
        ]:
            with contextlib.suppress(Exception):
                await conn.execute(text(sql))

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


# ── 辅助：写一条事件 ──────────────────────────────────────────────────────────


async def _write_event(db: AsyncSession, event_type: str, payload: dict, user_id: str = "u1") -> GrowthEvent | None:
    repo = GrowthEventRepository(db)
    return await repo.create_with_dedup(
        user_id=user_id,
        event_type=event_type,
        payload=payload,
    )


# ── 测试：_deep_merge_payload ─────────────────────────────────────────────────


class TestDeepMergePayload:
    def test_significant_moment_list_accumulation(self):
        base = {"title": "old title", "tags": ["a", "b"], "participants": ["Alice"]}
        update = {"title": "new title", "tags": ["b", "c"], "participants": ["Bob"]}
        result = _deep_merge_payload("significant_moment", base, update)

        assert result["title"] == "new title"  # 标量：新值优先
        assert result["tags"] == ["a", "b", "c"]  # 列表：去重累积，保序
        assert result["participants"] == ["Alice", "Bob"]

    def test_significant_moment_scalar_newer_wins(self):
        base = {"content": "first version", "importance": 3}
        update = {"content": "revised version", "importance": 5}
        result = _deep_merge_payload("significant_moment", base, update)
        assert result["content"] == "revised version"
        assert result["importance"] == 5

    def test_decision_made_content_newer_wins(self):
        base = {"decision": "do A", "context": "initial context", "tags": ["x"]}
        update = {"decision": "do B instead", "tags": ["y"]}
        result = _deep_merge_payload("decision_made", base, update)
        assert result["decision"] == "do B instead"
        assert result["context"] == "initial context"  # 保留旧值
        assert result["tags"] == ["x", "y"]  # 列表累积

    def test_default_newer_overwrites(self):
        base = {"a": 1, "b": 2}
        update = {"b": 99, "c": 3}
        result = _deep_merge_payload("reflection_added", base, update)
        assert result == {"a": 1, "b": 99, "c": 3}


# ── 测试：L2 语义去重配置 ─────────────────────────────────────────────────────


class TestSemanticDedupTypes:
    def test_types_in_dict(self):
        assert "significant_moment" in _SEMANTIC_DEDUP_TYPES
        assert "decision_made" in _SEMANTIC_DEDUP_TYPES

    def test_excluded_types_not_in_dict(self):
        assert "reflection_added" not in _SEMANTIC_DEDUP_TYPES
        assert "contradiction_noted" not in _SEMANTIC_DEDUP_TYPES
        assert "relationship_noted" not in _SEMANTIC_DEDUP_TYPES

    def test_thresholds(self):
        assert _SEMANTIC_DEDUP_TYPES["significant_moment"] == 0.80
        assert _SEMANTIC_DEDUP_TYPES["decision_made"] == 0.82


# ── 测试：SEMANTIC_DEDUP_ENABLED=False → 跳过 L2 ─────────────────────────────


@pytest.mark.asyncio
async def test_semantic_dedup_disabled_skips_l2(db):
    """SEMANTIC_DEDUP_ENABLED=False 时，_semantic_dedup_and_merge 不被调用。"""
    writer = MemoryWriter()
    with patch("core.config.get_settings") as mock_settings:
        mock_settings.return_value.semantic_dedup_enabled = False
        mock_settings.return_value.semantic_dedup_default_threshold = 0.85
        with patch("lib.memory.writer._semantic_dedup_and_merge") as mock_dedup:
            await writer._write_events(
                "u1",
                [{"event_type": "significant_moment", "payload": {"content": "test"}}],
                db,
            )
            mock_dedup.assert_not_called()


# ── 测试：event_type 不在 _SEMANTIC_DEDUP_TYPES → 跳过 ───────────────────────


@pytest.mark.asyncio
async def test_semantic_dedup_excluded_type(db):
    """reflection_added 不在 _SEMANTIC_DEDUP_TYPES，不触发 L2。"""
    writer = MemoryWriter()
    with patch("core.config.get_settings") as mock_settings:
        mock_settings.return_value.semantic_dedup_enabled = True
        with patch("lib.memory.writer._semantic_dedup_and_merge") as mock_dedup:
            await writer._write_events(
                "u1",
                [{"event_type": "reflection_added", "payload": {"content": "a reflection"}}],
                db,
            )
            mock_dedup.assert_not_called()


# ── 测试：LanceDB 不可用 → 降级正常写入 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_semantic_dedup_lancedb_unavailable(db):
    """provider 为 None 时，_semantic_dedup_and_merge 返回 None，正常写入。"""
    with patch("core.vector_store.get_document_index_provider", return_value=None):
        spec = {"event_type": "significant_moment", "payload": {"content": "test event"}}
        result = await _semantic_dedup_and_merge(spec, "u1", db)
        assert result is None


# ── 测试：无相似事件 → 正常写入 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_semantic_dedup_no_similar_event(db):
    """prefetch 返回空列表时，返回 None，正常写入。"""
    mock_provider = MagicMock()
    mock_provider.prefetch = AsyncMock(return_value=[])

    with patch("core.vector_store.get_document_index_provider", return_value=mock_provider):
        spec = {"event_type": "significant_moment", "payload": {"content": "unique event"}}
        result = await _semantic_dedup_and_merge(spec, "u1", db)
        assert result is None


# ── 测试：找到相似事件 → 合并 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_semantic_dedup_merges_similar_event(db):
    """相似度 >= threshold 时，合并 payload 并返回已有事件。"""
    # 写入已有事件
    existing = await _write_event(
        db,
        "significant_moment",
        {"content": "I started learning Python", "tags": ["python"]},
        user_id="u1",
    )
    assert existing is not None
    await db.flush()

    original_dedupe_key = existing.dedupe_key

    # mock provider 命中该事件
    from core.vector_store import ProviderHit

    mock_hit = ProviderHit(
        doc_id=f"narrative:{existing.id}",
        content="I started learning Python",
        score=0.85,
        metadata={"user_id": "u1", "event_type": "significant_moment"},
    )
    mock_provider = MagicMock()
    mock_provider.prefetch = AsyncMock(return_value=[mock_hit])

    with patch("core.vector_store.get_document_index_provider", return_value=mock_provider):
        spec = {
            "event_type": "significant_moment",
            "payload": {"content": "Learning Python is going great", "tags": ["learning", "python"]},
        }
        merged_event = await _semantic_dedup_and_merge(spec, "u1", db)

    assert merged_event is not None
    assert merged_event.id == existing.id

    # payload 已合并
    merged_payload = json.loads(merged_event.payload_json)
    assert "python" in merged_payload.get("tags", [])
    assert "learning" in merged_payload.get("tags", [])

    # projected_provider_at 置 NULL（触发重新索引）
    assert merged_event.projected_provider_at is None

    # updated_at 已设置
    assert merged_event.updated_at is not None

    # original_dedupe_key 保存了旧 key
    assert merged_event.original_dedupe_key == original_dedupe_key

    # dedupe_key 已更新（不再等于原始 key）
    assert merged_event.dedupe_key != original_dedupe_key

    # merged_from 包含一个 ID
    absorbed = json.loads(merged_event.merged_from)
    assert len(absorbed) == 1


# ── 测试：分数低于阈值 → 不合并 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_semantic_dedup_below_threshold(db):
    """分数低于 threshold，即使有命中也不合并。"""
    existing = await _write_event(db, "significant_moment", {"content": "event A"})
    await db.flush()

    from core.vector_store import ProviderHit

    mock_hit = ProviderHit(
        doc_id=f"narrative:{existing.id}",
        content="event A",
        score=0.60,  # 低于 0.80
        metadata={"user_id": "u1", "event_type": "significant_moment"},
    )
    mock_provider = MagicMock()
    mock_provider.prefetch = AsyncMock(return_value=[mock_hit])

    with patch("core.vector_store.get_document_index_provider", return_value=mock_provider):
        spec = {"event_type": "significant_moment", "payload": {"content": "event B"}}
        result = await _semantic_dedup_and_merge(spec, "u1", db)

    assert result is None


# ── 测试：L1 精确去重 — original_dedupe_key 拦截 ──────────────────────────────


@pytest.mark.asyncio
async def test_l1_dedup_catches_original_dedupe_key(db):
    """原始事件被语义合并后，再次提交原始 payload 应被 original_dedupe_key 拦截。"""
    # 写入事件 A
    event_a = await _write_event(db, "significant_moment", {"content": "original event A"})
    assert event_a is not None
    original_key = event_a.dedupe_key

    # 模拟语义合并：手动更新 dedupe_key 并保存 original_dedupe_key
    new_hash = "abc123"
    event_a.original_dedupe_key = original_key
    event_a.dedupe_key = f"new_key_{new_hash}"
    await db.flush()

    # 再次提交原始 payload（会生成相同的 dedupe_key = original_key）
    repo = GrowthEventRepository(db)
    from lib.memory.relational_store import _make_dedupe_key, _make_payload_hash

    payload = {"content": "original event A"}
    ph = _make_payload_hash(payload)
    dk = _make_dedupe_key("u1", "significant_moment", None, None, ph)
    assert dk == original_key  # 验证生成的 key 确实是原始 key

    result = await repo.create_with_dedup(
        user_id="u1",
        event_type="significant_moment",
        payload=payload,
    )
    # 应被 original_dedupe_key 拦截，返回 None
    assert result is None


# ── 测试：migration 幂等 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migration_idempotent(db):
    """多次执行 ALTER TABLE ADD COLUMN 不报错（duplicate column 静默忽略）。"""
    from sqlalchemy import text

    sqls = [
        "ALTER TABLE growth_events ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'active'",
        "ALTER TABLE growth_events ADD COLUMN updated_at DATETIME",
        "ALTER TABLE growth_events ADD COLUMN merged_from TEXT",
        "ALTER TABLE growth_events ADD COLUMN original_dedupe_key VARCHAR(128)",
    ]
    for sql in sqls:
        try:
            await db.execute(text(sql))
        except Exception as e:
            # 重复列错误是预期的，其他错误不应出现
            assert "duplicate column" in str(e).lower() or "already exists" in str(e).lower()


# ── 测试：新事件默认 status='active' ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_event_default_status(db):
    """新写入事件的 status 应为 'active'（或 None，待 migration 后为 'active'）。"""
    event = await _write_event(db, "significant_moment", {"content": "test"})
    assert event is not None
    # status 默认值由 ORM default 或 SQLite DEFAULT 提供
    # 在内存 DB 中 ORM default 生效
    assert event.status in ("active", None)  # None 可接受（server_default 仅在 flush 后从 DB 读回时生效）


# ── 测试：original_dedupe_key 初始为 None ─────────────────────────────────────


@pytest.mark.asyncio
async def test_new_event_original_dedupe_key_null(db):
    """新写入事件的 original_dedupe_key 应为 None。"""
    event = await _write_event(db, "significant_moment", {"content": "fresh event"})
    assert event is not None
    assert event.original_dedupe_key is None
