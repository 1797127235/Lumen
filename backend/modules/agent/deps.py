"""PydanticAI Agent 依赖类型定义"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession  # pyright: ignore[reportMissingImports]


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
    - tool_state: 工具运行时状态（循环检测计数器、pending_event_ids 等）。
    - usage_budget: 工具调用预算（次数限制等）。
    - trace_sink: 工具调用 trace 收集器。
    - workspace_root: 工具运行时工作区根目录（新架构使用）。
    """

    user_id: str
    db: AsyncSession
    conversation_id: str | None = None
    current_user_input: str | None = None
    pending_event_ids: list[str] = field(default_factory=list, repr=False, compare=False)
    build_context_cache: str = field(default="", repr=False, compare=False)
    agent_generation: int = 0
    # 工具运行时状态（跨调用共享，用于循环检测、预算、trace）
    tool_state: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    usage_budget: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    trace_sink: list[dict] = field(default_factory=list, repr=False, compare=False)
    # 工具运行时工作区根目录（新架构使用）
    workspace_root: Any = field(default=None, repr=False, compare=False)
