"""成长事件模型 — GrowthEvent"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.db import Base


class GrowthEvent(Base):
    __tablename__ = "growth_events"
    __table_args__ = (
        Index("ix_growth_events_user_event", "user_id", "event_type"),
        Index("ix_growth_events_user_entity", "user_id", "entity_type", "entity_id"),
        Index("ix_growth_events_dedupe", "user_id", "dedupe_key"),
        Index("ix_growth_events_unprojected_md", "user_id", "projected_md_at"),
        Index("ix_growth_events_unprojected_cognee", "user_id", "projected_cognee_at"),
        UniqueConstraint("user_id", "dedupe_key", name="uq_growth_events_user_dedupe"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="用户主动")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    projected_md_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    projected_cognee_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<GrowthEvent {self.event_type} user={self.user_id} at {self.created_at}>"
