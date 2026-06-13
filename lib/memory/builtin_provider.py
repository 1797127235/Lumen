"""内置 Provider — 文件-backed 有界记忆存储。

始终存在，直接操作 memory.md / about_you.md，不依赖外部服务。
"""

from __future__ import annotations

from typing import Any

from lib.memory.markdown import AsyncMarkdownStore
from lib.memory.provider import MemoryProvider
from shared.logging import get_logger

logger = get_logger(__name__)


class BuiltinMemoryProvider(MemoryProvider):
    """内置文件记忆 Provider。

    - system_prompt_block() 返回 about_you.md(+memory.md) 冻结快照（L0）。
    - prefetch() 对 memory.md 做简单文本匹配，返回相关段落。
    - sync_turn() 空操作（内置记忆不自动保存每轮对话）。
    - get_tool_schemas() 返回空列表（核心记忆工具直接操作 markdown）。
    """

    def __init__(self) -> None:
        super().__init__()
        self._store = AsyncMarkdownStore()

    @property
    def name(self) -> str:
        return "builtin"

    async def is_available(self) -> bool:
        return True

    async def initialize(self, session_id: str, **kwargs) -> None:
        pass

    async def system_prompt_block(self, **kwargs: Any) -> str:
        """返回 L0 冻结快照内容。

        由调用方（MemoryManager / AgentRunner）按 conversation 缓存。
        本方法只负责读取文件内容。
        """
        user_id = kwargs.get("user_id", "")
        if not user_id:
            return ""
        snapshot = await self._store.load_frozen_snapshot(user_id)
        if not snapshot:
            return ""
        return snapshot

    async def prefetch(self, query: str, *, session_id: str = "", **kwargs) -> str:
        """简单文本匹配：读 memory.md 全文，按段落过滤关键词。

        返回匹配的段落拼接文本。
        """
        user_id = kwargs.get("user_id", "")
        if not user_id or not query:
            return ""

        content = await self._store.read_memory(user_id)
        if not content:
            return ""

        # 简单分词：按空格分割 query 取关键词
        keywords = [kw.lower() for kw in query.split() if len(kw) > 1]
        if not keywords:
            return ""

        # 按段落分割（双换行），逐段匹配
        paragraphs = content.split("\n\n")
        matched: list[str] = []
        for para in paragraphs:
            para_lower = para.lower()
            if any(kw in para_lower for kw in keywords):
                matched.append(para)

        if not matched:
            return ""

        return "\n\n".join(matched)

    async def sync_turn(self, user: str, assistant: str, *, session_id: str = "") -> None:
        """内置文件记忆不自动保存每轮对话；空操作。"""
        pass

    async def get_tool_schemas(self) -> list[dict]:
        return []

    async def shutdown(self) -> None:
        pass
