"""MemoryProvider 抽象基类 — Hermes 风格插件子系统接口。

除 name(property) 外全部 async（Lumen 是 asyncio 进程）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MemoryProvider(ABC):
    """记忆 Provider 抽象基类。

    分为核心生命周期方法（必须实现）和可选钩子（子类重写启用）。
    """

    # ── 核心属性 ──

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 唯一标识名（如 builtin / honcho / mem0）。"""
        ...

    # ── 核心生命周期 ──

    @abstractmethod
    async def is_available(self) -> bool:
        """Provider 是否可用（外部服务连通性检查）。"""
        ...

    @abstractmethod
    async def initialize(self, session_id: str, **kwargs: Any) -> None:
        """初始化 provider（会话开始时调用）。"""
        ...

    async def system_prompt_block(self) -> str:
        """返回注入 system prompt 的静态块。

        builtin 返回 about_you.md(+memory.md) 冻结快照；
        外部 provider 返回自我介绍。
        """
        return ""

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        """根据 query 预取相关记忆上下文（L2 动态召回）。"""
        return ""

    async def queue_prefetch(self, query: str, *, session_id: str = "") -> None:  # noqa: B027
        """为下一回合排队后台预取。"""

    async def sync_turn(self, user: str, assistant: str, *, session_id: str = "") -> None:  # noqa: B027
        """同步一轮对话到 provider（assistant turn 结束后调用）。"""

    @abstractmethod
    async def get_tool_schemas(self) -> list[dict]:
        """返回该 provider 暴露的工具 schema 列表。"""
        ...

    async def handle_tool_call(self, tool_name: str, args: dict, **kwargs: Any) -> str:
        """处理该 provider 的工具调用。"""
        return ""

    async def shutdown(self) -> None:  # noqa: B027
        """关闭 provider（应用退出时调用）。"""

    # ── 可选钩子 ──

    async def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None:  # noqa: B027
        """新一轮用户输入开始时调用。"""

    async def on_session_end(self, messages: list[dict]) -> None:  # noqa: B027
        """会话结束时调用。"""

    async def on_session_switch(self, new_session_id: str, *, reset: bool = False, **kwargs: Any) -> None:  # noqa: B027
        """切换会话时调用。"""

    async def on_pre_compress(self, messages: list[dict]) -> str:
        """对话压缩/摘要前调用，返回追加到压缩 prompt 的文本。"""
        return ""

    async def on_memory_write(self, action: str, target: str, content: str, metadata: dict | None = None) -> None:  # noqa: B027
        """记忆写入事件镜像（builtin 写入时转发给外部 provider）。"""

    async def on_delegation(self, task: str, result: str, **kwargs: Any) -> None:  # noqa: B027
        """Agent 完成委派任务后调用。"""

    async def get_config_schema(self) -> list[dict]:
        """返回该 provider 的配置项 schema（用于设置 UI）。"""
        return []

    async def save_config(self, values: dict, lumen_home: str) -> None:  # noqa: B027
        """保存 provider 配置。"""


class NoOpMemoryProvider(MemoryProvider):
    """空操作 Provider — 当 memory.provider 未配置时兜底使用。"""

    @property
    def name(self) -> str:
        return "noop"

    async def is_available(self) -> bool:
        return True

    async def initialize(self, session_id: str, **kwargs: Any) -> None:
        pass

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    async def get_tool_schemas(self) -> list[dict]:
        return []

    async def handle_tool_call(self, tool_name: str, args: dict, **kwargs: Any) -> str:
        return ""
