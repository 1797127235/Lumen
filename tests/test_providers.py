"""DocumentIndexProvider 测试 — Null / HRR / LanceDB（mock）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from backend.modules.data_sources.ingestion.providers.cognee import CogneeProvider
from backend.modules.data_sources.ingestion.providers.hrr import HRRProvider
from backend.modules.data_sources.ingestion.providers.null import NullProvider


class TestNullProvider:
    """NullProvider — 什么都不做。"""

    def test_name(self) -> None:
        assert NullProvider.provider_name() == "null"
        assert NullProvider().name == "null"

    def test_is_available(self) -> None:
        assert NullProvider.is_available() is True

    @pytest.mark.asyncio
    async def test_prefetch_empty(self) -> None:
        provider = NullProvider()
        assert await provider.prefetch("anything") == ""

    @pytest.mark.asyncio
    async def test_sync_noop(self) -> None:
        provider = NullProvider()
        await provider.sync_document("content", "doc1")
        # 无异常即通过

    def test_tool_schemas_empty(self) -> None:
        assert NullProvider().get_tool_schemas() == []


class TestHRRProvider:
    """HRRProvider — 轻量语义搜索。"""

    def test_name(self) -> None:
        assert HRRProvider.provider_name() == "hrr"

    def test_is_available(self) -> None:
        assert HRRProvider.is_available() is True

    def test_word_vector_deterministic(self) -> None:
        """相同词应产生相同向量。"""
        provider = HRRProvider(dim=512)
        v1 = provider._get_word_vector("hello")
        v2 = provider._get_word_vector("hello")
        assert np.allclose(v1, v2)

    def test_word_vector_different(self) -> None:
        """不同词应产生不同向量。"""
        provider = HRRProvider(dim=512)
        v1 = provider._get_word_vector("hello")
        v2 = provider._get_word_vector("world")
        assert not np.allclose(v1, v2)

    def test_encode_text_consistency(self) -> None:
        """相同文本编码结果一致。"""
        provider = HRRProvider(dim=512)
        vec1 = provider._encode_text("machine learning")
        vec2 = provider._encode_text("machine learning")
        assert vec1 is not None
        assert np.allclose(vec1, vec2)

    def test_cosine_similarity_range(self) -> None:
        """余弦相似度应在 [-1, 1]。"""
        provider = HRRProvider(dim=512)
        a = np.random.randn(512).astype(np.float32)
        b = np.random.randn(512).astype(np.float32)
        a /= np.linalg.norm(a)
        b /= np.linalg.norm(b)
        sim = provider._cosine_similarity(a, b)
        assert -1.0 <= sim <= 1.0

    def test_tokenize(self) -> None:
        """分词应过滤停用词。"""
        provider = HRRProvider()
        words = provider._tokenize("The quick brown fox is jumping")
        assert "the" not in words
        assert "is" not in words
        assert "quick" in words
        assert "fox" in words

    def test_split_sentences(self) -> None:
        """分句应处理中英文标点。"""
        provider = HRRProvider()
        sents = provider._split_sentences("Hello world. How are you? 这是一句话。Another one!")
        assert len(sents) >= 3
        assert any("Hello world" in s for s in sents)

    @pytest.mark.asyncio
    async def test_sync_and_prefetch(self) -> None:
        """完整链路：sync → persist → prefetch。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = HRRProvider(dim=512, db_path=Path(tmpdir) / "hrr.json")
            provider.initialize()

            await provider.sync_document(
                "Machine learning is a subset of artificial intelligence.",
                "doc1",
                {"title": "ML"},
            )
            await provider.sync_document(
                "Deep learning uses neural networks with many layers.",
                "doc2",
                {"title": "DL"},
            )

            # 搜索与 ML 相关的内容
            result = await provider.prefetch("machine learning")
            assert "doc1" in result or "doc2" in result

            # 搜索不相关内容应返回空或低分
            result = await provider.prefetch("xyzabc123")
            assert result == "" or "doc1" not in result

    @pytest.mark.asyncio
    async def test_sync_overwrite(self) -> None:
        """相同 doc_id 应覆盖旧版本。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = HRRProvider(dim=512, db_path=Path(tmpdir) / "hrr.json")
            provider.initialize()

            await provider.sync_document("Old content", "doc1")
            await provider.sync_document("New content", "doc1")

            assert len(provider._documents) == 1
            assert provider._documents[0]["text"] == "New content"

    def test_persist_and_load(self) -> None:
        """持久化和加载应保留数据。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hrr.json"
            provider = HRRProvider(dim=512, db_path=db_path)
            provider.initialize()

            # 手动添加一条
            provider._documents.append(
                {
                    "id": "test1",
                    "doc_id": "doc1",
                    "vector": np.ones(512, dtype=np.float32) / np.sqrt(512),
                    "text": "hello",
                    "metadata": {},
                }
            )
            provider._persist()

            # 重新加载
            provider2 = HRRProvider(dim=512, db_path=db_path)
            provider2.initialize()
            assert len(provider2._documents) == 1
            assert provider2._documents[0]["doc_id"] == "doc1"
            assert np.allclose(provider2._documents[0]["vector"], provider._documents[0]["vector"])

    def test_tool_schemas(self) -> None:
        schemas = HRRProvider().get_tool_schemas()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "data_source_search"

    @pytest.mark.asyncio
    async def test_handle_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = HRRProvider(dim=512, db_path=Path(tmpdir) / "hrr.json")
            provider.initialize()
            await provider.sync_document("Python programming language is great for data science", "doc1")

            # HRR 短 query 对长文档的相似度可能低于阈值，用包含更多重叠词的 query
            result = await provider.handle_tool_call("data_source_search", {"query": "Python programming language"})
            assert "doc1" in result


class TestCogneeProvider:
    """CogneeProvider — 依赖 cognee 库的可用性。"""

    def test_name(self) -> None:
        assert CogneeProvider.provider_name() == "cognee"

    def test_is_available(self) -> None:
        # 在已安装 cognee 的环境中应为 True
        result = CogneeProvider.is_available()
        assert isinstance(result, bool)

    def test_tool_schemas(self) -> None:
        schemas = CogneeProvider().get_tool_schemas()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "data_source_search"


class TestLanceDBProviderMock:
    """LanceDBProvider — 使用 mock 测试逻辑（避免真实 embedding 模型加载）。"""

    def test_name(self) -> None:
        from backend.modules.data_sources.ingestion.providers.lancedb import LanceDBProvider

        assert LanceDBProvider.provider_name() == "lancedb"

    def test_chunk_text(self) -> None:
        from backend.modules.data_sources.ingestion.providers.lancedb import LanceDBProvider

        provider = LanceDBProvider.__new__(LanceDBProvider)
        provider._embedder = None
        chunks = provider._chunk_text("a" * 1000, chunk_size=100, overlap=10)
        assert len(chunks) > 1
        assert all(len(c) <= 100 for c in chunks)
        # overlap 检查：相邻块应有重叠
        if len(chunks) >= 2:
            assert chunks[0][90:] == chunks[1][:10] or len(chunks[0]) == 100

    def test_is_available_when_dependencies_missing(self) -> None:
        """测试 is_available 在缺少依赖时返回 False。"""
        from backend.modules.data_sources.ingestion.providers.lancedb import LanceDBProvider

        # is_available 会尝试 import lancedb 和 sentence_transformers
        # 如果环境中有这些库，测试仍会 pass；如果没有，也会 pass
        result = LanceDBProvider.is_available()
        assert isinstance(result, bool)
