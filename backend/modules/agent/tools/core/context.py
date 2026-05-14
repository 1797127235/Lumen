"""工具运行时上下文 — 承载会话级状态与安全边界。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession  # pyright: ignore[reportMissingImports]


@dataclass
class ToolRuntimeContext:
    """每次 Agent Run 的运行时上下文。
    任何路径型工具必须从这里拿运行时根目录，不允许自行 fallback 到 HOME。
    """

    session_id: str = ""
    """当前请求/会话 ID。"""

    conversation_id: str = ""
    """对话 ID。"""

    user_id: str = "demo_user"
    """用户 ID。"""

    workspace_root: Path | None = None
    """安全边界：工具可访问的根目录。

    若缺失，PathPolicy 直接报配置错误，不允许退回 HOME。
    """

    cwd: Path | None = None
    """当前工作目录：相对路径解析的基准。

    默认等于 workspace_root。
    """

    db: AsyncSession | None = None
    """数据库会话（如需持久化）。"""

    tool_state: dict[str, Any] = field(default_factory=dict)
    """运行时会话状态：循环检测计数器、预算等。"""

    usage_budget: dict[str, Any] = field(default_factory=dict)
    """使用预算：调用次数、token 限制等。"""

    trace_sink: list[dict] = field(default_factory=list)
    """Trace 收集器：记录每次工具调用的完整信息。"""

    request_metadata: dict[str, Any] = field(default_factory=dict)
    """请求级元数据：model_version、agent_generation 等。"""

    def __post_init__(self) -> None:
        if self.cwd is None and self.workspace_root is not None:
            self.cwd = self.workspace_root
