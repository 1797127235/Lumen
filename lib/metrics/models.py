"""统一时间序列表：所有埋点事件落在一张 metric_events 表里。

设计权衡：
- labels_json 用 TEXT 而非 SQLAlchemy JSON 列 —— SQLite 兼容、序列化在 Python 侧控制
- value 统一 Float —— 计数=1.0 / 耗时=ms / token 数=int as float
- 不为每类指标建独立表，新增指标只需加一个 record() 调用
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class MetricEvent(Base):
    """单条指标事件。name 区分指标类型，labels_json 携带细分维度。"""

    __tablename__ = "metric_events"
    __table_args__ = (
        Index("ix_metric_events_name_created", "name", "created_at"),
        Index("ix_metric_events_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # 指标名，如 llm.tokens.input / turn.duration_ms / tool.calls
    name: Mapped[str] = mapped_column(String(64), index=True)
    # 计数=1.0 / 耗时 ms / token 数
    value: Mapped[float] = mapped_column(Float, default=0.0)
    # {"model":"...","tool":"...","channel":"...","status":"ok|error"} 等
    labels_json: Mapped[str] = mapped_column(Text, default="{}")
    # 可选关联（主动消息/后台任务可能无 conversation_id）
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    def __repr__(self) -> str:
        return f"<MetricEvent {self.name}={self.value} @ {self.created_at}>"
