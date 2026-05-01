"""对话会话与消息"""

import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, func, JSON, Text, Integer, Float, ForeignKey
from sqlalchemy import Enum as SAEnum, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.backend.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str | None] = mapped_column(String(200))
    topic_type: Mapped[str | None] = mapped_column(
        String(30)
    )  # career_consult | learning | resume | interview | emotional | technical_qa
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | closed | archived
    context_snapshot: Mapped[dict | None] = mapped_column(JSON)
    message_count: Mapped[int] = mapped_column(default=0)
    is_pinned: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    message_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.conversation_id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | system | tool
    content: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(
        String(20), default="text"
    )  # text | code | image | card | file
    card_type: Mapped[str | None] = mapped_column(String(50))
    card_payload: Mapped[dict | None] = mapped_column(JSON)
    intent: Mapped[str | None] = mapped_column(String(50))
    sentiment: Mapped[float | None] = mapped_column(Float)
    tokens_used: Mapped[int | None] = mapped_column(Integer)
    model_version: Mapped[str | None] = mapped_column(String(50))
    feedback_rating: Mapped[int | None] = mapped_column(Integer)
    feedback_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
