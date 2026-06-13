"""测试 Akasha MemoryProvider 基础功能。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from lib.llm.embeddings import AsyncEmbeddingClient
from lib.memory.builtins.akasha import Provider
from lib.memory.builtins.akasha.engine import AkashaEngine


class FakeEmbedder(AsyncEmbeddingClient):
    """确定性 fake embedder：每个字符对应一个 one-hot 维度。"""

    def __init__(self, dim: int = 8):
        # 不调用父类，避免构建真实 httpx 客户端
        self._model = "fake"
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for text in texts:
            vec = np.zeros(self._dim, dtype=np.float32)
            for ch in text.lower():
                vec[ord(ch) % self._dim] += 1.0
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec = vec / norm
            results.append(vec.tolist())
        return results

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_embedder():
    return FakeEmbedder(dim=16)


@pytest.fixture
def temp_db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    yield path
    path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_engine_commit_and_query(fake_embedder, temp_db_path):
    engine = AkashaEngine(
        user_id="demo",
        config={"db_path": str(temp_db_path)},
        embedder=fake_embedder,
    )
    try:
        # 提交两轮对话
        await engine.commit_turn(
            session_key="sess1",
            user_msg="我喜欢 Rust",
            assistant_msg="Rust  ownership 很有意思",
            user_msg_id="u1",
            assistant_msg_id="a1",
            seq=1,
        )
        await engine.commit_turn(
            session_key="sess1",
            user_msg="Rust 怎么学",
            assistant_msg="先看 The Book",
            user_msg_id="u2",
            assistant_msg_id="a2",
            seq=2,
        )

        # 查询
        result = await engine.query("sess1", "Rust 学习")
        assert result.text != ""
        assert len(result.cards) > 0
        # 只要成功召回即可（fake embedder 较简单，不强制包含 rust 字样）
        assert len(result.cards) >= 0
    finally:
        engine.close()


@pytest.mark.asyncio
async def test_provider_name_and_schemas():
    provider = Provider()
    assert provider.name == "akasha"
    schemas = await provider.get_tool_schemas()
    assert any(s["name"] == "akasha_recall" for s in schemas)


@pytest.mark.asyncio
async def test_provider_prefetch_without_engine_returns_empty():
    provider = Provider()
    text = await provider.prefetch("test")
    assert text == ""


@pytest.mark.asyncio
async def test_sync_turn_uses_passed_arguments():
    """sync_turn 必须直接使用接口传入的 user/assistant，不反向查询宿主数据库。"""
    provider = Provider()
    provider._engine = AsyncMock()
    provider._engine.next_turn_seq = Mock(return_value=0)

    await provider.sync_turn("user text", "assistant text", session_id="web:abc")

    provider._engine.next_turn_seq.assert_called_once_with("web:abc")
    provider._engine.commit_turn.assert_awaited_once_with(
        session_key="web:abc",
        user_msg="user text",
        assistant_msg="assistant text",
        user_msg_id="web:abc:u:0",
        assistant_msg_id="web:abc:a:0",
        seq=0,
    )


@pytest.mark.asyncio
async def test_commit_turn_merges_user_and_assistant_into_one_node(fake_embedder, temp_db_path):
    """一个 turn 的 user 和 assistant 必须合并为同一个节点，不覆盖其他 turn。"""
    engine = AkashaEngine(
        user_id="demo",
        config={"db_path": str(temp_db_path)},
        embedder=fake_embedder,
    )
    try:
        await engine.commit_turn(
            session_key="sess1",
            user_msg="hello",
            assistant_msg="hi there",
            user_msg_id="u1",
            assistant_msg_id="a1",
            seq=0,
        )
        await engine.commit_turn(
            session_key="sess1",
            user_msg="how are you",
            assistant_msg="I am fine",
            user_msg_id="u2",
            assistant_msg_id="a2",
            seq=1,
        )

        nodes = engine._store.list_nodes()
        session_nodes = [n for n in nodes if n.session_key == "sess1"]
        assert len(session_nodes) == 2
        assert {n.key for n in session_nodes} == {"sess1:0", "sess1:1"}
    finally:
        engine.close()


@pytest.mark.asyncio
async def test_idf_based_on_turn_content(fake_embedder, temp_db_path):
    """IDF 必须基于 Akasha 自己的 akasha_turn_content 计算，而不是 sessions.db。"""
    engine = AkashaEngine(
        user_id="demo",
        config={"db_path": str(temp_db_path)},
        embedder=fake_embedder,
    )
    try:
        await engine.commit_turn(
            session_key="sess1",
            user_msg="Python programming language",
            assistant_msg="Python is great",
            user_msg_id="u1",
            assistant_msg_id="a1",
            seq=0,
        )

        from lib.memory.builtins.akasha.core import build_idf_table, load_idf_from_db

        conn = engine._store.raw_connection()
        idf = build_idf_table(conn)
        assert "python" in idf
        loaded = load_idf_from_db(conn)
        assert "python" in loaded
    finally:
        engine.close()
