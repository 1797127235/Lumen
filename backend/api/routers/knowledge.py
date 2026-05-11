"""知识库 API — 路由层，业务逻辑委托到 application"""

from __future__ import annotations

import asyncio
import hashlib
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.application.knowledge_service import process_file as _process_file_async
from backend.db import get_db
from backend.logging_config import get_logger
from backend.utils.parsers import SUPPORTED_EXTENSIONS

logger = get_logger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Form("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """上传文件 → 存原始文件 + 创建 UploadedFile(status=pending)。
    后台异步处理，前端轮询状态。"""
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, "文件大小超过 10MB 限制")
    if not file.filename:
        raise HTTPException(400, "缺少文件名")

    ext = _get_ext(file.filename)
    if f".{ext}" not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"不支持的文件格式 .{ext}")

    # 存原始文件
    from backend.memory.documents import DocumentStore

    file_hash = hashlib.sha256(content).hexdigest()
    doc_store = DocumentStore()
    storage_path = doc_store.save(user_id, "uploaded", file.filename, content)

    # 创建 UploadedFile 记录
    from backend.domain.models import UploadedFile

    record = UploadedFile(
        id=str(uuid.uuid4()),
        user_id=user_id,
        filename=file.filename,
        file_type=ext,
        file_hash=file_hash,
        size_bytes=len(content),
        storage_path=storage_path,
        status="pending",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    # 后台处理（fire-and-forget）
    asyncio.create_task(  # noqa: RUF006
        _process_file(
            record_id=record.id,
            user_id=user_id,
            filename=file.filename,
            file_type=ext,
            content=content,
            file_hash=file_hash,
            storage_path=storage_path,
        )
    )

    return {"id": record.id, "filename": file.filename, "status": "pending"}


async def _process_file(
    record_id: str,
    user_id: str,
    filename: str,
    file_type: str,
    content: bytes,
    file_hash: str,
    storage_path: str,
) -> None:
    """后台异步处理 — 委托到 application 层。"""
    await _process_file_async(
        record_id=record_id,
        user_id=user_id,
        filename=filename,
        file_type=file_type,
        content=content,
        file_hash=file_hash,
        storage_path=storage_path,
    )


@router.get("/list")
async def list_files(
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """列出用户已上传的文件。"""
    from backend.domain.models import UploadedFile

    result = await db.execute(
        select(UploadedFile).where(UploadedFile.user_id == user_id).order_by(UploadedFile.created_at.desc())
    )
    files = result.scalars().all()
    return {
        "files": [_file_to_dict(f) for f in files],
        "total": len(files),
    }


@router.get("/{file_id}/status")
async def get_file_status(
    file_id: str,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """轮询单个文件的处理状态。"""
    from backend.domain.models import UploadedFile

    record = await db.get(UploadedFile, file_id)
    if not record or record.user_id != user_id:
        raise HTTPException(404, "文件不存在")
    return {
        "id": str(record.id),
        "status": record.status,
        "chunk_count": record.chunk_count,
        "preview": record.preview,
        "error_message": record.error_message,
    }


@router.delete("/{file_id}")
async def delete_file(
    file_id: str,
    user_id: str = Query("demo_user"),
    db: AsyncSession = Depends(get_db),
):
    """删除文件记录 + 原始文件 + 关联的 GrowthEvent。

    TODO: Cognee 向量索引中的 chunk 数据目前无法精确清理，
    因为 SemanticStore 尚未提供按 doc_id 删除的能力。
    删除后如需完全清理语义索引，可调用记忆重置或重建。
    """
    from backend.domain.models import UploadedFile

    record = await db.get(UploadedFile, file_id)
    if not record or record.user_id != user_id:
        raise HTTPException(404, "文件不存在")

    # 删原始文件
    from backend.memory.documents import DocumentStore

    DocumentStore().delete(record.storage_path)

    # 删关联事件
    if record.event_id:
        from backend.memory.facade import get_memory

        await get_memory().delete_event(user_id, record.event_id)

    await db.delete(record)
    await db.commit()

    # 重建投影
    from backend.memory.facade import get_memory

    await get_memory().force_md_rebuild(user_id)

    return {"deleted": True}


def _file_to_dict(f) -> dict:
    return {
        "id": str(f.id),
        "filename": f.filename,
        "file_type": f.file_type,
        "size_bytes": f.size_bytes,
        "status": f.status,
        "chunk_count": f.chunk_count,
        "preview": f.preview,
        "error_message": f.error_message,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _get_ext(filename: str) -> str:
    from pathlib import PurePosixPath

    return PurePosixPath(filename).suffix.lower().lstrip(".") or "unknown"
