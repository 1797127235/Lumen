"""PydanticAI Agent 依赖类型定义"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class LumenDeps:
    """Lumen Agent 依赖注入类型
    用于 PydanticAI Agent 的 RunContext，提供：
    - user_id: 用户 ID
    - db: SQLAlchemy 异步会话
    - conversation_id: 会话 ID（用于加载历史消息）
    - current_user_input: 当前用户输入（用于长期记忆召回）
    - pending_event_ids: 本轮工具创建的事件 ID，commit 后触发投影用。
    - build_context_cache: 同一 agent run 内缓存 build_context 结果，
      避免 tool call 重复读取 .md 文件。Prefetch Lifecycle 模式。
    - agent_generation: 创建 deps 时 Agent 的代际号，用于检测请求执行期间
      Agent 是否被重建（如 LLM 配置变更）。
    """

    user_id: str
    db: AsyncSession
    conversation_id: str | None = None
    current_user_input: str | None = None
    pending_event_ids: list[str] = field(default_factory=list, repr=False, compare=False)
    build_context_cache: str = field(default="", repr=False, compare=False)
    agent_generation: int = 0
