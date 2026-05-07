"""memory_search 工具 — Agent 记忆搜索。

scope 参数映射到 Cognee datasets，Agent 根据对话上下文选择搜索范围。
"""

from __future__ import annotations

from pydantic_ai import Agent, RunContext

from app.backend.agent.deps import LumenDeps
from app.backend.logging_config import get_logger

logger = get_logger(__name__)


def register(agent: Agent[LumenDeps, str]) -> None:
    @agent.tool
    async def memory_search(
        ctx: RunContext[LumenDeps],
        query: str,
        scope: str | None = None,
    ) -> str:
        """搜索记忆。
        scope 选择规则（有明确范围时填，否则不传）：
        - "profile"   — 技能/经历/画像/目标/学校等个人档案
        - "emotions"  — 情绪/焦虑/心情/日记/内心想法
        - "reference" — 公司信息/行业报告/学长经验/外部资料
        - "chat"      — 历史对话摘要
        - 不传（None）— 跨领域或不确定时，搜全部
        """
        logger.info("Tool call: memory_search", query=query, scope=scope)

        if not query or not query.strip():
            return "请提供搜索关键词。"

        from app.backend.memory import get_memory
        from app.backend.memory.cognee_admin.datasets import SCOPE_DATASETS

        datasets = SCOPE_DATASETS.get(scope) if scope else None

        memory = get_memory()
        items = await memory.recall(ctx.deps.user_id, query, datasets=datasets)
        if items:
            return "\n".join(
                f"- [{item.categories[0] if item.categories else '?'}] {item.content[:300]}" for item in items
            )
        return "未找到相关内容。"
