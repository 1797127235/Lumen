"""用户模型 — User + UserProfile"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.db import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    email: Mapped[str | None] = mapped_column(String(100), unique=True)
    nickname: Mapped[str | None] = mapped_column(String(50))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="active")
    user_type: Mapped[str] = mapped_column(String(30), default="student")
    privacy_level: Mapped[str] = mapped_column(String(10), default="standard")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[UserProfile | None] = relationship(back_populates="user", uselist=False)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"), unique=True)
    school_name: Mapped[str | None] = mapped_column(String(100))
    school_level: Mapped[str | None] = mapped_column(String(30))
    major: Mapped[str | None] = mapped_column(String(50))
    grade: Mapped[str | None] = mapped_column(String(20))
    graduation_year: Mapped[int | None] = mapped_column()
    target_direction: Mapped[str | None] = mapped_column(String(50))
    target_company_level: Mapped[str | None] = mapped_column(String(20))
    current_skills: Mapped[list | None] = mapped_column(JSON)
    profile_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    available_time_daily: Mapped[int | None] = mapped_column()
    personality_tags: Mapped[dict | None] = mapped_column(JSON)
    learning_style: Mapped[str | None] = mapped_column(String(20))
    anxiety_level: Mapped[int | None] = mapped_column()
    preferred_interaction: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="profile")
