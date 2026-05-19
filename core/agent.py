"""Lumen Agent — 核心编排，对应 openhanako core/agent.js"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from sqlalchemy.ext.asyncio import AsyncSession  # pyright: ignore[reportMissingImports]

from core.config import get_settings
from lib.chat.models import Conversation
from lib.memory import get_memory
from shared.logging import get_logger

logger = get_logger(__name__)


# ════════════════════════════
#  依赖注入类型
# ════════════════════════════


@dataclass
class LumenDeps:
    """PydanticAI RunContext 依赖，贯穿整个 agent run。"""

    user_id: str
    db: AsyncSession
    conversation_id: str | None = None
    current_user_input: str | None = None
    pending_event_ids: list[str] = field(default_factory=list, repr=False, compare=False)
    build_context_cache: str = field(default="", repr=False, compare=False)
    agent_generation: int = 0
    tool_state: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    usage_budget: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    trace_sink: list[dict] = field(default_factory=list, repr=False, compare=False)
    workspace_root: Any = field(default=None, repr=False, compare=False)


# ════════════════════════════
#  Agent 类
# ════════════════════════════


class LumenAgent:
    """Lumen agent 实例，持有 PydanticAI Agent 缓存及其构建逻辑。"""

    def __init__(self) -> None:
        self._agent: Agent[LumenDeps, str] | None = None
        self._config_hash: str = ""
        self._generation: int = 0

    # ────────────────────────
    #  公开接口
    # ────────────────────────

    def get(self) -> Agent[LumenDeps, str]:
        """返回缓存的 Agent；config 或工具指纹变化时自动重建。"""
        fp = self._config_fingerprint()
        if self._agent is not None and self._config_hash == fp:
            return self._agent
        self._agent = self.create()
        self._config_hash = fp
        self._generation += 1
        logger.info("Agent 已重建", generation=self._generation)
        return self._agent

    def build_system_prompt(self) -> str:
        """组装静态 system prompt（对应 openhanako buildSystemPrompt 静态前缀）。

        静态内容放前面，动态内容（记忆、时间戳）由 @agent.system_prompt 装饰器追加在末尾，
        最大化跨 session 的 KV cache 命中率。
        """

        def section(title: str, content: str) -> str:
            return f"\n\n---\n\n{title}\n\n{content}"

        parts = [
            "你是「Lumen」，用户的 AI 伴侣。说话像一个真正认识你的朋友，不是客服，不奉承。",
        ]

        parts.append(
            section(
                "## 工具使用",
                "用户分享个人信息时立即用 update_profile / memory_save 保存；"
                "需要回忆时用 memory_search。\n"
                "搜不到如实说，别编；搜完空结果也要告诉用户「没找到相关内容」，不要沉默。\n"
                "调用工具前先说你在做什么（哪怕一句），别闷声执行。",
            )
        )

        parts.append(
            section(
                "## 对话风格",
                "开场白简短自然，不罗列功能。回复直接开始，不要以逗号或其他标点符号打头。",
            )
        )

        parts.append(
            section(
                "## 记忆使用",
                "记忆和用户档案是你内化的背景知识，你和用户是认识很久的人，这些事你本来就知道。"
                "记忆的存在感应该是零，它的作用应该是满的。\n\n"
                "- **只有当用户提到了相关内容，记忆才参与进来。** 用户没有提起的话题，不要主动从记忆里翻出来讲。"
                "记忆参与的方式是无声的：影响你的角度、语气、判断，但不出现在文字里。\n"
                "- **永远不要让用户感觉到「记忆」这个东西的存在。** "
                "禁止使用「我记得」「你之前说过」「根据记忆」这类表述，除非用户主动问「你还记得 xxx 吗」。\n"
                "- **记忆可能过时，当前对话永远优先。** 信息冲突时以对话为准，不要用旧记忆纠正用户。",
            )
        )

        return "".join(parts)

    def create(self) -> Agent[LumenDeps, str]:
        """创建一个新的 PydanticAI Agent 实例。"""
        from pydantic_ai import Agent, RunContext

        from lib.tools.factory import assemble_tools, build_pydantic_toolset

        model = self._create_model()
        all_toolsets = [build_pydantic_toolset(assemble_tools())]

        agent = Agent(
            model=model,
            deps_type=LumenDeps,
            output_type=str,
            system_prompt=self.build_system_prompt(),
            retries=2,
            end_strategy="graceful",
            toolsets=all_toolsets,
        )

        @agent.system_prompt
        async def dynamic_prompt(ctx: RunContext[LumenDeps]) -> str:
            # system[1]: 记忆上下文 — 5 分钟 TTL 内保持不变，可被 KV cache 命中
            conversation_summary: str | None = None
            try:
                conv = await ctx.deps.db.get(Conversation, ctx.deps.conversation_id)
                if conv and conv.summary:
                    conversation_summary = conv.summary
            except Exception:
                pass

            memory_instance = get_memory()
            context = await memory_instance.build_context(
                ctx.deps.user_id,
                user_input=ctx.deps.current_user_input,
                conversation_summary=conversation_summary,
            )

            if context.strip():
                ctx.deps.build_context_cache = context
                return f"\n\n---\n\n# 用户记忆\n\n{context}"
            return (
                "\n\n【用户画像为空】用户尚未提供个人信息。"
                "当用户提供信息时，调用 memory_save 或 update_profile 保存。"
            )

        @agent.system_prompt
        async def timestamp_prompt(_ctx: RunContext[LumenDeps]) -> str:
            # system[2]: 时间戳 — 单独一条 system message，每分钟变化
            # 拆分后 system[0]+[1] 不受时间戳影响，对话历史可持续命中 cache
            from datetime import datetime

            return f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"

        return agent

    @property
    def generation(self) -> int:
        """当前 Agent 代际号，每次重建递增。"""
        return self._generation

    # ────────────────────────
    #  内部方法
    # ────────────────────────

    def _create_model(self) -> OpenAIChatModel:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        settings = get_settings()
        provider = settings.llm_provider
        model_name = settings.llm_model
        api_key = settings.llm_api_key or settings.dashscope_api_key
        base_url = settings.llm_base_url

        if not api_key:
            raise ValueError(
                "未配置 LLM API Key。请在设置页面配置 API Key，"
                "或在 .env 文件中设置 DASHSCOPE_API_KEY 或 LLM_API_KEY。"
            )
        if not base_url:
            raise ValueError(
                f"未配置 LLM Base URL。请在设置页面配置 Base URL，"
                f"或在 .env 文件中设置 LLM_BASE_URL（当前 provider: {provider}）。"
            )

        logger.info("创建模型", provider=provider, model=model_name, base_url=base_url, has_key=bool(api_key))

        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(base_url=base_url, api_key=api_key),
        )

    def _config_fingerprint(self) -> str:
        """计算配置指纹（LLM + MCP 工具），变化时触发 Agent 重建。"""
        s = get_settings()
        raw = (
            f"{s.llm_provider}|{s.llm_model}"
            f"|{s.llm_api_key or s.dashscope_api_key}"
            f"|{s.llm_base_url}"
            f"|{self._tool_fingerprint()}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def _tool_fingerprint(self) -> str:
        """计算已连接 MCP 工具的指纹；新增/删除 MCP server 时 Agent 自动重建。"""
        try:
            from lib.tools.mcp.client_manager import get_mcp_manager

            mcp_tools: list[str] = []
            for server_name, tools in get_mcp_manager().discover_tools():
                for t in tools:
                    mcp_tools.append(f"{server_name}:{t['name']}")
            return hashlib.sha256("|".join(sorted(mcp_tools)).encode()).hexdigest()[:16]
        except Exception:
            return "v1"


# ════════════════════════════
#  模块级单例 + 便捷函数
# ════════════════════════════

_lumen_agent = LumenAgent()


def get_agent() -> Agent[LumenDeps, str]:
    return _lumen_agent.get()


def create_agent() -> Agent[LumenDeps, str]:
    return _lumen_agent.create()


def get_agent_generation() -> int:
    return _lumen_agent.generation


def create_model() -> OpenAIChatModel:
    """创建 LLM 模型实例（供非 agent 场景使用，如 memory understanding）。"""
    return _lumen_agent._create_model()
