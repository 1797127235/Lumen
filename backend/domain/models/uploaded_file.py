"""上传文件模型 — UploadedFile"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.db import Base


class UploadedFile(Base):
    """用户上传的文件实体 — 独立于 GrowthEvent 的文件生命周期管理。"""

    __tablename__ = "uploaded_files"
    __table_args__ = (
        Index("ix_uploaded_files_user_id", "user_id"),
        Index("ix_uploaded_files_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # pdf/docx/md/...
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA256
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # pending → processing → ready / failed
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 关联的 GrowthEvent ID
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
