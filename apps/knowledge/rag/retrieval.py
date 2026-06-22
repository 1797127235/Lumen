"""高级检索策略：MQE（多查询扩展）+ HyDE（假设文档嵌入）。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from ..embeddings import AsyncEmbeddingClient

logger = logging.getLogger(__name__)


class Retriever(Protocol):
    """检索器接口。"""

    async def retrieve(self, query_vec: list[float], top_k: int) -> list[Any]: ...


@dataclass
class RetrieveOptions:
    """检索选项。"""

    top_k: int = 4
    enable_mqe: bool = False
    mqe_expansions: int = 3
    enable_hyde: bool = False
    candidate_pool_multiplier: int = 4
    score_threshold: float = 0.1


@dataclass
class RetrievalHit:
    """检索结果。"""

    chunk_id: str
    document_id: str
    text: str
    score: float
    document_name: str


async def generate_mqe_queries(query: str, n: int, llm_client: Any, model: str) -> list[str]:
    """MQE 多查询扩展：生成语义等价但表述不同的查询。"""
    try:
        messages = [
            {
                "role": "system",
                "content": "你是检索查询扩展助手。生成语义等价或互补的多样化查询。使用中文，简短，避免标点。",
            },
            {"role": "user", "content": f"原始查询：{query}\n请给出{n}个不同表述的查询，每行一个。"},
        ]
        response = await llm_client.chat.completions.create(model=model, messages=messages, temperature=0.7)
        text = response.choices[0].message.content or ""
        lines = [re.sub(r"^[\s\-•*\d.、)]+", "", line).strip() for line in text.split("\n") if line.strip()]
        return lines[:n]
    except Exception as e:
        logger.debug("MQE 查询扩展失败: %s", e)
        return []


async def generate_hyde_doc(query: str, llm_client: Any, model: str) -> str | None:
    """HyDE 假设文档嵌入：让 LLM 生成假设性答案用于检索。"""
    try:
        messages = [
            {
                "role": "system",
                "content": "根据用户问题，先写一段可能的答案性段落，用于向量检索的查询文档（不要分析过程）。",
            },
            {"role": "user", "content": f"问题：{query}\n请直接写一段中等长度、客观、包含关键术语的段落。"},
        ]
        response = await llm_client.chat.completions.create(model=model, messages=messages, temperature=0.7)
        text = (response.choices[0].message.content or "").strip()
        return text if text else None
    except Exception as e:
        logger.debug("HyDE 生成失败: %s", e)
        return None


async def retrieve_expanded(
    question: str,
    opts: RetrieveOptions,
    embedder: AsyncEmbeddingClient,
    retrieve_fn: Any,
    llm_client: Any | None = None,
    llm_model: str = "",
) -> list[RetrievalHit]:
    """扩展检索：扩展-检索-合并三步流程。"""
    if not question.strip():
        return []

    # 1. 收集扩展查询
    expansions: list[str] = [question]

    if opts.enable_mqe and opts.mqe_expansions > 0 and llm_client:
        mqe = await generate_mqe_queries(question, opts.mqe_expansions, llm_client, llm_model)
        expansions.extend(mqe)

    if opts.enable_hyde and llm_client:
        hyde = await generate_hyde_doc(question, llm_client, llm_model)
        if hyde:
            expansions.append(hyde)

    # 去重
    unique: list[str] = []
    for q in expansions:
        if q and q.strip() and q not in unique:
            unique.append(q)
    final_expansions = unique if unique else [question]

    # 2. 分配候选池，逐个查询检索
    pool = max(opts.top_k * opts.candidate_pool_multiplier, 20)
    per = max(1, pool // len(final_expansions))

    all_hits: list[RetrievalHit] = []
    for q in final_expansions:
        try:
            qv = await embedder.embed_one(q)
            hits = await retrieve_fn(qv, per)
            all_hits.extend(hits)
        except Exception as e:
            logger.debug("扩展查询检索失败: %s", e)

    # 3. 去重合并
    merged: dict[str, RetrievalHit] = {}
    for hit in all_hits:
        existing = merged.get(hit.chunk_id)
        if not existing or hit.score > existing.score:
            merged[hit.chunk_id] = hit

    # 4. 排序、阈值过滤、取 topK
    sorted_hits = sorted(merged.values(), key=lambda h: h.score, reverse=True)
    filtered = [h for h in sorted_hits if h.score >= opts.score_threshold]
    return filtered[: opts.top_k]
