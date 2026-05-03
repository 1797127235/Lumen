"""知识库文档与文本块模型"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.backend.db.base import Base


class Document(Base):
    """知识库文档 — 存储原始文档/文件的元数据"""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(255))
    # 来源类型: knowledge_base(系统知识库), file_upload(用户上传), external_url(外部链接)
    source_type: Mapped[str] = mapped_column(String(30), default="knowledge_base")
    source_path: Mapped[str | None] = mapped_column(String(500))  # 文件路径或 URL，可为空（如纯文本录入）
    category: Mapped[str | None] = mapped_column(
        String(30)
    )  # career_path | skill | learning | interview | industry | resume
    file_type: Mapped[str | None] = mapped_column(String(10))  # pdf | docx | txt | md | json
    # 文档处理状态机
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | processing | indexed | error
    error_message: Mapped[str | None] = mapped_column(Text)  # 处理失败时的错误信息
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 文本块数量（由服务层同步维护，必须与 len(chunks) 保持一致）
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    # 上传者（系统知识库为空，用户上传时记录 owner）
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.user_id"), index=True)
    # 扩展元数据（如作者、标签、关键词、原始文件名等）
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: {})
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # 关联文本块（一对多，级联删除）
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    """文档文本块 — 切割后的检索单元"""

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)  # 块文本内容
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)  # 在文档中的序号
    token_count: Mapped[int | None] = mapped_column(Integer)  # token 数量（可选，用于统计）
    # 块级元数据（如所属章节、关键词、摘要等）
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: {})
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 关联文档（多对一）
    document: Mapped["Document"] = relationship(back_populates="chunks")

    # 约束：同一文档下 chunk_index 唯一，防止并发写入重复
    __table_args__ = (UniqueConstraint("document_id", "chunk_index", name="uq_chunk_document_index"),)
