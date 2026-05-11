"""知识库 & 文件上传 API schemas"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FilePayload(BaseModel):
    filename: str
    file_type: str  # "pdf" | "docx" | "md" | ...
    file_hash: str = ""
    size_bytes: int = 0
    storage_path: str = ""  # DocumentStore 相对路径
    chunk_count: int = 0
    preview: str = ""  # 前 200 字预览
    metadata: dict = Field(default_factory=dict)


class KnowledgeFileResponse(BaseModel):
    """单个文件的状态响应。"""

    id: str
    filename: str
    file_type: str
    size_bytes: int
    status: str  # pending | processing | ready | failed
    chunk_count: int = 0
    preview: str | None = None
    error_message: str | None = None
    created_at: str | None = None


class KnowledgeListResponse(BaseModel):
    """文件列表响应。"""

    files: list[KnowledgeFileResponse]
    total: int


class KnowledgeUploadResponse(BaseModel):
    """上传提交响应 — 立即返回，前端轮询状态。"""

    id: str
    filename: str
    status: str  # "pending"
