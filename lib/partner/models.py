"""Lumen 伙伴系统 — SQLAlchemy ORM 模型"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class LumenState(Base):
    __tablename__ = "lumen_state"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, default="demo_user")
    mood: Mapped[str] = mapped_column(String, default="calm")
    mood_intensity: Mapped[float] = mapped_column(Float, default=0.5)
    pending_mood: Mapped[str | None] = mapped_column(String, nullable=True)
    pending_count: Mapped[int] = mapped_column(Integer, default=0)
    derived_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class LumenThought(Base):
    __tablename__ = "lumen_thoughts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, default="demo_user")
    content: Mapped[str] = mapped_column(Text)
    source_event_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    judge_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_veto: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate: Mapped[int] = mapped_column(Integer, default=0)
    mood: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
