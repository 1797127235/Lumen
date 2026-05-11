"""memory_search 工具 — Agent 记忆搜索。

支持两种搜索模式（借鉴 akashic-agent）：
- keyword: FTS5 关键词匹配（默认，搜 Narrative 事件）
- grep: 时间范围过滤（不依赖搜索，适合「最近做了什么」类查询）
"""

from __future__ import annotations

from pydantic_ai import Agent, RunContext

from backend.agent.deps import LumenDeps
from backend.logging_config import get_logger
from backend.memory import get_memory
from backend.memory.datasets import SCOPE_DATASETS

logger = get_logger(__name__)


def register(agent: Agent[LumenDeps, str]) -> None:
    @agent.tool
    async def memory_search(
        ctx: RunContext[LumenDeps],
        query: str,
        scope: str | None = None,
        search_mode: str = "keyword",
        time_filter: str | None = None,
    ) -> str:
        """搜索记忆。

        search_mode 选择：
        - "keyword"（默认）— 关键词搜索，适用于「Python」「实习」等具体词
        - "grep" — 时间范围浏览，适用于「最近做了什么」「这周」等自然语言，
          必须配合 time_filter 使用

        time_filter（仅 grep 模式生效）：
        - "today" / "yesterday" / "recent_3d" / "recent_7d" / "recent_30d"
        - "YYYY-MM-DD~YYYY-MM-DD" 绝对范围

        scope（仅 keyword 模式生效）：
        - "profile"   — 技能/经历/画像/目标/学校等
        - "emotions"  — 情绪/焦虑/心情/日记
        - "reference" — 公司/行业/学长经验
        - "chat"      — 历史对话摘要
        - 不传（None）— 搜索全部
        """
        logger.info(
            "Tool call: memory_search",
            query=query,
            scope=scope,
            search_mode=search_mode,
            time_filter=time_filter,
        )

        if not query or not query.strip():
            return "请提供搜索关键词。"

        datasets = SCOPE_DATASETS.get(scope) if scope else None

        memory_instance = get_memory()
        items = await memory_instance.recall(
            ctx.deps.user_id,
            query,
            datasets=datasets,
            search_mode=search_mode,
            time_filter=time_filter,
        )
        if items:
            return "\n".join(
                f"- [{item.categories[0] if item.categories else '?'}] {item.content[:300]}" for item in items
            )
        return "未找到相关内容。"
