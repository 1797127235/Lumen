"""RAG 服务：导入流水线 + 检索拼装。"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..embeddings import AsyncEmbeddingClient, build_embedder
from ..storage.kb_store import KbDocument, SQLiteKbStore
from ..storage.vector_store import ChunkInput, SQLiteVectorStore
from .chunker import ChunkOptions, chunk_text
from .extractors import PdfExtractResult, extract_document, is_supported_format
from .retrieval import (
    RetrievalHit,
    RetrieveOptions,
    retrieve_expanded,
)

logger = logging.getLogger(__name__)

ImportProgress = Callable[[str], None]


@dataclass
class KnowledgeStores:
    """知识库存储集合。"""

    kb: SQLiteKbStore
    vector: SQLiteVectorStore


_stores: KnowledgeStores | None = None


def init_stores(kb: SQLiteKbStore, vector: SQLiteVectorStore) -> None:
    """初始化存储实例。"""
    global _stores
    _stores = KnowledgeStores(kb=kb, vector=vector)


def get_stores() -> KnowledgeStores:
    """获取存储实例。"""
    if _stores is None:
        raise RuntimeError("存储未初始化，请先调用 init_stores()")
    return _stores


def file_checksum(file_path: str) -> str:
    """计算文件校验和。"""
    data = Path(file_path).read_bytes()
    return hashlib.sha256(data).hexdigest()[:16]


def _apply_score_threshold(hits: list[RetrievalHit], score_threshold: float, top_k: int) -> list[RetrievalHit]:
    """按相关度阈值过滤并裁剪结果。"""
    return [hit for hit in hits if hit.score >= score_threshold][:top_k]


async def import_document(
    file_path: str,
    file_name: str | None = None,
    embedder: AsyncEmbeddingClient | None = None,
    on_progress: ImportProgress | None = None,
    chunk_options: ChunkOptions | None = None,
) -> KbDocument:
    """导入文档到知识库。

    完整流水线：解析 → 分块 → embedding → 入库 → 标记 ready
    """
    stores = get_stores()
    report = on_progress or (lambda msg: None)

    if not is_supported_format(file_path):
        raise ValueError("不支持的文件格式。支持: pdf, txt, md, docx, html")

    report("正在读取文件…")
    checksum = file_checksum(file_path)

    name = file_name or Path(file_path).name
    doc = await stores.kb.create_kb_document(name, file_path, checksum)

    try:
        report("正在解析文档…")
        result = await extract_document(file_path)

        if isinstance(result, PdfExtractResult):
            text = result.text
            report(f"已解析 {result.pages} 页，约 {len(text)} 字")
        else:
            text = result
            report(f"已解析，约 {len(text)} 字")

        report("正在分块…")
        chunks = chunk_text(text, chunk_options)
        if not chunks:
            raise ValueError("文档内容为空或无法提取有效文本")
        report(f"共分为 {len(chunks)} 个文本块")

        if embedder is None:
            embedder = build_embedder()

        report("正在向量化…")
        vectors = await embedder.embed([c.text for c in chunks])
        if len(vectors) != len(chunks):
            raise ValueError("向量化数量与分块数量不一致")

        report("正在写入知识库…")
        await stores.vector.insert_chunks(
            doc.id,
            [
                ChunkInput(
                    text=c.text,
                    embedding=v,
                    index=c.index,
                    token_count=c.token_count,
                )
                for c, v in zip(chunks, vectors, strict=True)
            ],
        )

        await stores.kb.set_kb_document_status(doc.id, "ready", len(chunks))
        report(f"导入完成：{len(chunks)} 个文本块")

        return await stores.kb.get_kb_document(doc.id)  # type: ignore

    except Exception as e:
        await stores.kb.set_kb_document_status(doc.id, "failed")
        report(f"导入失败：{e}")
        raise


async def retrieve_for_query(
    question: str,
    embedder: AsyncEmbeddingClient | None = None,
    opts: RetrieveOptions | None = None,
    llm_client: Any | None = None,
    llm_model: str = "",
) -> list[RetrievalHit]:
    """检索与问题相关的 Top-K 分块。"""
    stores = get_stores()

    if embedder is None:
        embedder = build_embedder()

    resolved = opts or RetrieveOptions()

    async def plain_retrieve(query_vec: list[float], top_k: int) -> list[RetrievalHit]:
        return await stores.vector.retrieve(query_vec, top_k)

    if not resolved.enable_mqe and not resolved.enable_hyde:
        qv = await embedder.embed_one(question)
        hits = await plain_retrieve(qv, resolved.top_k)
        return _apply_score_threshold(hits, resolved.score_threshold, resolved.top_k)

    try:
        return await retrieve_expanded(
            question,
            resolved,
            embedder,
            plain_retrieve,
            llm_client,
            llm_model,
        )
    except Exception as e:
        logger.warning("扩展检索失败，降级为纯向量: %s", e)
        qv = await embedder.embed_one(question)
        hits = await plain_retrieve(qv, resolved.top_k)
        return _apply_score_threshold(hits, resolved.score_threshold, resolved.top_k)
