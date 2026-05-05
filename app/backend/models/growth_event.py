"""GrowthEvent — 成长事件表，SQLite 真相层的核心表"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.backend.db.base import Base


class GrowthEvent(Base):
    """成长事件：记录用户的所有成长轨迹

    这是 SQLite 真相层的核心表，所有成长轨迹都从这里投影到 .md 和 Cognee。
    事件驱动写入，不是逐对话提取。

    字段说明：
    - dedupe_key: 去重键，格式 "{event_type}:{entity_type}:{entity_id}"，用于精确去重
    - payload_hash: payload 的 SHA256 哈希，用于内容级去重
    - projected_md_at: 投影到 .md 文件的时间，NULL 表示未投影
    - projected_cognee_at: 投影到 Cognee 的时间，NULL 表示未投影
    """

    __tablename__ = "growth_events"
    __table_args__ = (
        Index("ix_growth_events_user_event", "user_id", "event_type"),
        Index("ix_growth_events_user_entity", "user_id", "entity_type", "entity_id"),
        Index("ix_growth_events_dedupe", "user_id", "dedupe_key"),
        Index("ix_growth_events_unprojected_md", "user_id", "projected_md_at"),
        Index("ix_growth_events_unprojected_cognee", "user_id", "projected_cognee_at"),
        # 唯一约束：防止 TOCTOU 竞态导致的重复事件
        UniqueConstraint("user_id", "dedupe_key", name="uq_growth_events_user_dedupe"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        # "profile_updated" | "skill_added" | "skill_level_changed" |
        # "target_created" | "target_status_changed" |
        # "reflection_added" | "project_added" | "resume_uploaded"
    )
    entity_type: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        # "profile" | "skill" | "target" | "reflection" | "project"
    )
    entity_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        # 关联实体的 ID
    )
    payload_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        # 事件详情的 JSON 字符串
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="user主动",
        # "user主动" | "对话识别" | "简历提取" | "系统产出"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    # ── 新增字段：去重和投影追踪 ─────────────────────

    dedupe_key: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        # 去重键，格式 "{event_type}:{entity_type}:{entity_id}"
        # 用于精确去重，避免 7 天窗口误杀
    )
    payload_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        # payload_json 的 SHA256 哈希
        # 用于内容级去重
    )
    projected_md_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        # 投影到 .md 文件的时间
        # NULL 表示未投影，需要重新投影
    )
    projected_cognee_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        # 投影到 Cognee 的时间
        # NULL 表示未投影，需要重新投影
    )

    def __repr__(self) -> str:
        return f"<GrowthEvent {self.event_type} user={self.user_id} at {self.created_at}>"
