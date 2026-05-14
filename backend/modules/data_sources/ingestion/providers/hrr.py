"""HRRProvider — DocumentIndexProvider 的 Holographic Reduced Representations 实现。

零外部依赖（除 numpy），轻量语义搜索。适合资源受限或不想装大型模型包的场景。

HRR 原理：
- 每个词/概念用高维随机向量表示（~2048 维）
- 句子/文档的表示 = 组成词向量的叠加（sum）+ 位置绑定（circular convolution）
- 相似度计算 = 余弦相似度

这不是深度学习的语义向量，而是符号向量的统计近似。效果不如 embedding model，
但零配置、零下载、毫秒级初始化。
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np

from backend.core.config import USER_DATA_DIR
from backend.core.logging import get_logger
from backend.modules.data_sources.ingestion.document_index_provider import DocumentIndexProvider

logger = get_logger(__name__)

DEFAULT_DIM = 2048


@functools.lru_cache(maxsize=4096)
def _make_word_vector(word: str, dim: int) -> np.ndarray:
    """获取词的 HRR 向量（全局缓存，避免内存无限增长）。"""
    seed = int(hashlib.sha256(word.encode()).hexdigest(), 16) % (2**31)
    rng = np.random.RandomState(seed)
    vec = rng.randn(dim).astype(np.float32)
    vec = vec / (np.linalg.norm(vec) + 1e-8)
    return vec


@functools.lru_cache(maxsize=512)
def _make_position_vector(pos: int, dim: int) -> np.ndarray:
    """位置向量：用不同频率的正弦波编码位置（全局缓存）。"""
    vec = np.zeros(dim, dtype=np.float32)
    for k in range(dim):
        vec[k] = math.sin(pos / (10000 ** (k / dim)))
    vec = vec / (np.linalg.norm(vec) + 1e-8)
    return vec


class HRRProvider(DocumentIndexProvider):
    """HRR 实现。零外部依赖（仅需 numpy），轻量语义搜索。

    分块策略：句子级分块（句号/换行分隔）。
    表示：HRR 叠加 + 位置绑定。
    """

    @classmethod
    def provider_name(cls) -> str:
        return "hrr"

    @classmethod
    def is_available(cls) -> bool:
        try:
            import numpy as np  # noqa: F401

            return True
        except ImportError:
            return False

    def __init__(self, dim: int = DEFAULT_DIM, db_path: Path | None = None) -> None:
        self._dim = dim
        self._db_path = db_path or (USER_DATA_DIR / "hrr_index.json")
        self._documents: list[dict[str, Any]] = []

    def initialize(self) -> None:
        """加载已有索引（如果存在）。"""
        if self._db_path.exists():
            try:
                with open(self._db_path, encoding="utf-8") as f:
                    data = json.load(f)
                for row in data:
                    row["vector"] = np.array(row["vector"], dtype=np.float32)
                self._documents = data
                logger.info("hrr.index_loaded", count=len(self._documents))
            except Exception as exc:
                logger.warning("hrr.index_load_failed", error=str(exc))

    async def prefetch(self, query: str) -> str:
        """HRR 召回：query → HRR vector → 余弦相似度 top-k。"""
        if not self._documents:
            return ""

        query_vec = self._encode_text(query)
        if query_vec is None:
            return ""

        # 计算余弦相似度
        scores: list[tuple[float, dict[str, Any]]] = []
        for doc in self._documents:
            sim = self._cosine_similarity(query_vec, doc["vector"])
            scores.append((sim, doc))

        scores.sort(key=lambda x: x[0], reverse=True)
        top = scores[:10]

        if not top or top[0][0] < 0.05:
            return ""

        lines: list[str] = []
        for _, (sim, doc) in enumerate(top, 1):
            text = doc.get("text", "")
            doc_id = doc.get("doc_id", "unknown")
            lines.append(f"[来源: {doc_id} (相似度: {sim:.2f})]\n{text[:500]}")

        return "\n\n".join(lines)

    async def sync_document(
        self,
        content: str,
        doc_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """保存文档：分句 → 编码 → 平均 → 存储。"""
        sentences = self._split_sentences(content)
        if not sentences:
            return

        # 每个句子编码后取平均作为文档向量
        vecs = [self._encode_text(s) for s in sentences]
        vecs = [v for v in vecs if v is not None]
        if not vecs:
            return

        doc_vec = np.mean(vecs, axis=0)
        doc_vec = doc_vec / (np.linalg.norm(doc_vec) + 1e-8)

        # 去重：相同 doc_id 先删除旧版本
        self._documents = [d for d in self._documents if d["doc_id"] != doc_id]

        self._documents.append(
            {
                "id": hashlib.sha256(f"{doc_id}:{content[:100]}".encode()).hexdigest(),
                "doc_id": doc_id,
                "vector": doc_vec,
                "text": content[:50000],
                "metadata": metadata or {},
            }
        )

        await asyncio.to_thread(self._persist)
        logger.info("hrr.document_synced", doc_id=doc_id, sentences=len(sentences))

    def _encode_text(self, text: str) -> np.ndarray | None:
        """文本 → HRR 向量。"""
        words = self._tokenize(text)
        if not words:
            return None

        vectors: list[np.ndarray] = []
        for i, word in enumerate(words):
            vec = _make_word_vector(word, self._dim)
            pos_vec = _make_position_vector(i, self._dim)
            bound = self._circular_convolution(vec, pos_vec)
            vectors.append(bound)

        result = np.sum(vectors, axis=0)
        norm = np.linalg.norm(result)
        if norm > 0:
            result = result / norm
        return result

    def _get_word_vector(self, word: str) -> np.ndarray:
        """获取词的 HRR 向量（委托到全局缓存）。"""
        return _make_word_vector(word, self._dim)

    def _circular_convolution(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """循环卷积：HRR 绑定操作。"""
        # 时域等价：ifft(fft(a) * fft(b))
        A = np.fft.fft(a)
        B = np.fft.fft(b)
        return np.real(np.fft.ifft(A * B))

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """余弦相似度。"""
        dot = np.dot(a, b)
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        if norm == 0:
            return 0.0
        return float(dot / norm)

    def _tokenize(self, text: str) -> list[str]:
        """简单分词：CJK 单字 + ASCII 单词，过滤停用词。"""
        words = re.findall(r"[\u4e00-\u9fff\u3040-\u30ff]|[a-z0-9]+", text.lower())
        stopwords = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "must",
            "shall",
            "can",
            "need",
            "dare",
            "ought",
            "used",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "between",
            "under",
            "and",
            "but",
            "or",
            "yet",
            "so",
            "if",
            "because",
            "although",
            "though",
            "while",
            "where",
            "when",
            "that",
            "which",
            "who",
            "whom",
            "whose",
            "what",
            "this",
            "these",
            "those",
            "i",
            "you",
            "he",
            "she",
            "it",
            "we",
            "they",
            "me",
            "him",
            "her",
            "us",
            "them",
            "my",
            "your",
            "his",
            "its",
            "our",
            "their",
            "mine",
            "yours",
            "hers",
            "ours",
            "theirs",
            "myself",
            "yourself",
            "himself",
            "herself",
            "itself",
            "ourselves",
            "yourselves",
            "themselves",
        }
        return [w for w in words if w not in stopwords and len(w) > 1]

    def _split_sentences(self, text: str) -> list[str]:
        """简单分句。"""
        sentences = re.split(r"[。！？\.\?!\n]+", text)
        return [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]

    def _persist(self) -> None:
        """持久化到 JSON（HRR 向量可序列化）。"""
        data = []
        for doc in self._documents:
            row = {k: v for k, v in doc.items() if k != "vector"}
            row["vector"] = doc["vector"].tolist()
            data.append(row)
        with open(self._db_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "data_source_search",
                "description": "搜索外部数据源中的相关文档（HRR 语义搜索）",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            }
        ]

    async def handle_tool_call(self, name: str, args: dict) -> str:
        if name == "data_source_search":
            return await self.prefetch(args["query"])
        raise NotImplementedError(f"Tool {name} not supported")
