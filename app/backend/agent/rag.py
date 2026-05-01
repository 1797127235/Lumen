"""RAG 基础设施 — 知识库检索（MVP 简化版：不依赖向量库）"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.backend.agent.llm_router import embed


class SimpleRAG:
    """简化版 RAG：本地 JSON 知识库 + 向量相似度检索
    MVP 阶段先用内存 + JSON 文件，后续替换为 Milvus
    """

    def __init__(self, kb_path: str | None = None):
        self._docs: list[dict[str, Any]] = []
        self._embeddings: list[list[float]] = []
        if kb_path:
            self._load(kb_path)

    def _load(self, path: str):
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            self._docs = data if isinstance(data, list) else data.get("documents", [])

    async def add_document(self, doc: dict[str, Any]):
        """添加一条文档并计算向量"""
        text = doc.get("title", "") + " " + doc.get("content", "")
        vec = await embed(text)
        self._docs.append(doc)
        self._embeddings.append(vec)

    async def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """语义搜索，返回 top_k 条相关文档"""
        if not self._docs:
            return []

        query_vec = await embed(query)
        scores = []
        for i, doc_vec in enumerate(self._embeddings):
            score = self._cosine_sim(query_vec, doc_vec)
            scores.append((score, i))

        scores.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, idx in scores[:top_k]:
            results.append({**self._docs[idx], "_score": score})
        return results

    def _cosine_sim(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0


# 全局单例
_rag: SimpleRAG | None = None


def get_rag() -> SimpleRAG:
    global _rag
    if _rag is None:
        kb_path = str(Path(__file__).parents[3] / "data" / "knowledge_base.json")
        _rag = SimpleRAG(kb_path)
    return _rag


def init_rag(kb_path: str):
    global _rag
    _rag = SimpleRAG(kb_path)
