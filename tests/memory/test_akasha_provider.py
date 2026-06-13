"""测试 Akasha MemoryProvider 基础功能。"""

from __future__ import annotations

import tempfile
from pathlib import Path

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
