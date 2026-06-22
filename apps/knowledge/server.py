"""Knowledge Base MCP Server — 独立知识库模块。

启动：
    cd apps/knowledge
    python server.py

默认监听 127.0.0.1:8766/sse。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from apps.knowledge.rag.retrieval import RetrieveOptions
from apps.knowledge.rag.service import import_document, init_stores, retrieve_for_query
from apps.knowledge.storage import SQLiteKbStore, SQLiteVectorStore, close_db, get_db

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("knowledge")

HOST = os.environ.get("KNOWLEDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("KNOWLEDGE_PORT", "8766"))
DB_PATH = os.environ.get("KNOWLEDGE_DB_PATH", str(Path.home() / ".lumen" / "knowledge.db"))

mcp = FastMCP("lumen-knowledge")
mcp.settings.host = HOST
mcp.settings.port = PORT

_kb_store: SQLiteKbStore | None = None
_vector_store: SQLiteVectorStore | None = None


async def _init_storage() -> None:
    global _kb_store, _vector_store

    db = await get_db(DB_PATH)
    _kb_store = SQLiteKbStore(db)
    _vector_store = SQLiteVectorStore(db)
    init_stores(_kb_store, _vector_store)

    logger.info("Knowledge DB 已就绪: %s", DB_PATH)


def _require_kb_store() -> SQLiteKbStore:
    if _kb_store is None:
        raise RuntimeError("知识库未初始化")
    return _kb_store


async def _search_knowledge(
    query: str,
    top_k: int = 4,
    score_threshold: float = 0.1,
    enable_hyde: bool = False,
    enable_mqe: bool = False,
) -> dict[str, Any]:
    kb_store = _require_kb_store()

    chunk_count = await kb_store.count_chunks()
    if chunk_count == 0:
        return {"results": [], "message": "知识库为空，请先导入文档。"}

    opts = RetrieveOptions(
        top_k=top_k,
        enable_hyde=enable_hyde,
        enable_mqe=enable_mqe,
        score_threshold=score_threshold,
    )

    hits = await retrieve_for_query(query, opts=opts)
    if not hits:
        return {"results": [], "message": "未找到相关文档片段。"}

    results = [
        {
            "rank": i + 1,
            "score": round(hit.score, 3),
            "document_id": hit.document_id,
            "document_name": hit.document_name,
            "chunk_id": hit.chunk_id,
            "text": hit.text,
        }
        for i, hit in enumerate(hits)
    ]
    return {"results": results, "count": len(results)}


async def _list_documents(ready_only: bool = False) -> dict[str, Any]:
    kb_store = _require_kb_store()
    docs = await kb_store.list_kb_documents()
    if ready_only:
        docs = [doc for doc in docs if doc.status == "ready"]

    if not docs:
        return {"documents": [], "files": [], "message": "知识库为空"}

    documents = [
        {
            "id": doc.id,
            "name": doc.name,
            "path": doc.path,
            "status": doc.status,
            "chunks_count": doc.chunks_count,
            "checksum": doc.checksum,
            "created_at": doc.created_at,
        }
        for doc in docs
    ]
    return {"documents": documents, "files": documents, "count": len(documents)}


async def _delete_document(document_id: str) -> dict[str, Any]:
    kb_store = _require_kb_store()
    doc = await kb_store.get_kb_document(document_id)
    if not doc:
        return {"error": f"文档不存在: {document_id}"}

    await kb_store.delete_kb_document(document_id)
    return {
        "success": True,
        "deleted": doc.name,
        "chunks_removed": doc.chunks_count,
    }


async def _read_chunks(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    if not chunks:
        return {"error": "请提供 chunks 数组"}

    kb_store = _require_kb_store()

    grouped: dict[str, list[int]] = {}
    for item in chunks:
        document_id = str(item.get("document_id", ""))
        idx = item.get("chunk_index")
        if not document_id or idx is None:
            continue
        grouped.setdefault(document_id, []).append(int(idx))

    results = []
    for document_id, indices in grouped.items():
        doc = await kb_store.get_kb_document(document_id)
        if not doc:
            continue

        chunk_list = await kb_store.get_chunks_by_indices(document_id, indices)
        for chunk in chunk_list:
            results.append(
                {
                    "document_id": document_id,
                    "document_name": doc.name,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                    "token_count": chunk.token_count,
                }
            )

    return {"chunks": results, "count": len(results)}


# ═══════════════════════════════════════════════════════════
# MCP 工具
# ═══════════════════════════════════════════════════════════


@mcp.tool()
async def kb_search(
    query: str,
    top_k: int = 4,
    enable_hyde: bool = False,
    enable_mqe: bool = False,
    score_threshold: float = 0.1,
) -> dict[str, Any]:
    """在知识库中检索与问题相关的文档片段。

    Args:
        query: 检索问题
        top_k: 返回结果数
        enable_hyde: 是否启用 HyDE 扩展检索（需要后续接入 LLM client 才会扩展）
        enable_mqe: 是否启用 MQE 扩展检索（需要后续接入 LLM client 才会扩展）
        score_threshold: 最低相似度阈值
    """
    try:
        return await _search_knowledge(
            query=query,
            top_k=top_k,
            score_threshold=score_threshold,
            enable_hyde=enable_hyde,
            enable_mqe=enable_mqe,
        )
    except Exception as e:
        logger.error("kb_search 失败: %s", e, exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def kb_list_documents() -> dict[str, Any]:
    """列出知识库中所有已导入的文档。"""
    try:
        return await _list_documents()
    except Exception as e:
        logger.error("kb_list_documents 失败: %s", e, exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def kb_get_documents_meta(document_ids: list[str]) -> dict[str, Any]:
    """获取指定文档的元信息（支持批量）。

    Args:
        document_ids: 文档 ID 数组
    """
    try:
        if not document_ids:
            return {"error": "请提供 document_ids 数组"}

        kb_store = _require_kb_store()
        docs = await kb_store.get_kb_documents(document_ids)

        documents = [
            {
                "id": doc.id,
                "name": doc.name,
                "path": doc.path,
                "status": doc.status,
                "chunks_count": doc.chunks_count,
                "checksum": doc.checksum,
                "created_at": doc.created_at,
            }
            for doc in docs
        ]

        return {"documents": documents, "count": len(documents)}

    except Exception as e:
        logger.error("kb_get_documents_meta 失败: %s", e, exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def kb_read_chunks(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """读取指定文档的指定分块原文。

    Args:
        chunks: 要读取的分块列表，每项包含 document_id 和 chunk_index
    """
    try:
        return await _read_chunks(chunks)
    except Exception as e:
        logger.error("kb_read_chunks 失败: %s", e, exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def kb_import(
    file_path: str,
    file_name: str | None = None,
) -> dict[str, Any]:
    """导入文件到知识库（支持 pdf/txt/md/docx/html）。

    Args:
        file_path: 文件绝对路径
        file_name: 文件名（可选，默认从路径提取）
    """
    try:
        _require_kb_store()

        progress_msgs: list[str] = []

        def on_progress(msg: str) -> None:
            progress_msgs.append(msg)
            logger.info("导入进度: %s", msg)

        doc = await import_document(
            file_path=file_path,
            file_name=file_name,
            on_progress=on_progress,
        )

        return {
            "success": True,
            "document_id": doc.id,
            "document_name": doc.name,
            "chunks_count": doc.chunks_count,
            "status": doc.status,
            "progress": progress_msgs,
        }

    except Exception as e:
        logger.error("kb_import 失败: %s", e, exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def kb_delete_document(document_id: str) -> dict[str, Any]:
    """从知识库中删除指定文档及其所有分块。"""
    try:
        return await _delete_document(document_id)
    except Exception as e:
        logger.error("kb_delete_document 失败: %s", e, exc_info=True)
        return {"error": str(e)}


@mcp.tool()
async def kb_stats() -> dict[str, Any]:
    """获取知识库统计信息。"""
    try:
        kb_store = _require_kb_store()
        docs = await kb_store.list_kb_documents()
        total_chunks = await kb_store.count_chunks()

        return {
            "total_documents": len(docs),
            "ready_documents": len([d for d in docs if d.status == "ready"]),
            "processing_documents": len([d for d in docs if d.status == "processing"]),
            "failed_documents": len([d for d in docs if d.status == "failed"]),
            "total_chunks": total_chunks,
        }
    except Exception as e:
        logger.error("kb_stats 失败: %s", e, exc_info=True)
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════════


async def main() -> None:
    await _init_storage()

    logger.info("Knowledge MCP server 启动")
    logger.info("监听: http://%s:%d/sse", HOST, PORT)
    logger.info("数据库: %s", DB_PATH)

    try:
        await mcp.run_sse_async()
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
