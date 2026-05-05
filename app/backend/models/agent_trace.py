"""Agent 执行追踪 — 可观测性"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.backend.db.base import Base


class AgentTrace(Base):
    """Agent 执行步骤追踪

    用于记录 Agent 每一步的执行情况，便于：
    - 分析工具调用成功率
    - 追踪 LLM 延迟
    - 发现循环检测问题
    - 优化 Agent 性能
    """

    __tablename__ = "agent_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)

    # 步骤信息
    step_number: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(20))  # "llm_call" | "tool_call" | "tool_result"

    # 工具相关
    tool_name: Mapped[str | None] = mapped_column(String(50))
    tool_args: Mapped[dict | None] = mapped_column(JSON)
    tool_result: Mapped[str | None] = mapped_column(String(5000))

    # 执行信息
    content: Mapped[str] = mapped_column(String(5000))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(String(1000))

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<AgentTrace {self.id} step={self.step_number} type={self.step_type}>"
