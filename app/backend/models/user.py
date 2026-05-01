"""用户与用户画像"""

import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, func, JSON, Enum as SAEnum, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.backend.db.base import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    email: Mapped[str | None] = mapped_column(String(100), unique=True)
    nickname: Mapped[str | None] = mapped_column(String(50))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended | deleted
    user_type: Mapped[str] = mapped_column(
        String(30), default="student"
    )  # student | transfer_student | graduate
    privacy_level: Mapped[str] = mapped_column(String(10), default="standard")  # standard | high
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped["UserProfile | None"] = relationship(back_populates="user", uselist=False)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    profile_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.user_id"), unique=True)
    school_name: Mapped[str | None] = mapped_column(String(100))
    school_level: Mapped[str | None] = mapped_column(
        String(30)
    )  # 985 | 211 | double_first_class | normal
    major: Mapped[str | None] = mapped_column(String(50))
    grade: Mapped[str | None] = mapped_column(
        String(20)
    )  # freshman | sophomore | junior | senior | graduate1 | graduate2 | graduate3
    graduation_year: Mapped[int | None] = mapped_column()
    target_direction: Mapped[str | None] = mapped_column(String(50))
    target_company_level: Mapped[str | None] = mapped_column(
        String(20)
    )  # top | major | medium | state_owned
    current_skills: Mapped[dict | None] = mapped_column(JSON)
    available_time_daily: Mapped[int | None] = mapped_column()
    personality_tags: Mapped[dict | None] = mapped_column(JSON)
    learning_style: Mapped[str | None] = mapped_column(
        String(20)
    )  # video | reading | practice | mixed
    anxiety_level: Mapped[int | None] = mapped_column()
    preferred_interaction: Mapped[str | None] = mapped_column(
        String(10)
    )  # chat | detailed | brief
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="profile")
