"""PydanticAI Agent 依赖类型定义"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class CareerOSDeps:
    """CareerOS Agent 依赖注入类型

    用于 PydanticAI Agent 的 RunContext，提供：
    - user_id: 用户 ID
    - db: SQLAlchemy 异步会话
    - conversation_id: 会话 ID（用于加载历史消息）
    - current_user_input: 当前用户输入（用于长期记忆召回）
    - memory_tool_called: 本轮是否已调用记忆写入工具（memory_save / update_profile）。
      置位后，后台提取器跳过本轮，避免双写。
    """

    user_id: str
    db: AsyncSession
    conversation_id: str | None = None
    current_user_input: str | None = None
    memory_tool_called: bool = False
