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
    """

    user_id: str
    db: AsyncSession
