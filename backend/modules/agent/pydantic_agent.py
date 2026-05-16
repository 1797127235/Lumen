"""PydanticAI Agent 定义 — Lumen"""

from __future__ import annotations

import hashlib

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.modules.agent.deps import LumenDeps
from backend.modules.chat.models import Conversation
from backend.modules.memory import get_memory

logger = get_logger(__name__)

# Agent 缓存：config hash 不变时复用实例，避免每次重新注册 tools/dynamic_prompt
# _cached_agent_generation：每次重建递增，用于追踪请求是否使用陈旧 Agent
_cached_agent: Agent[LumenDeps, str] | None = None
_cached_config_hash: str = ""
_cached_agent_generation: int = 0

# 新工具运行时（全局单例，懒加载）
_tool_runtime: tuple | None = None
_tool_runtime_hash: str = ""


def _get_tool_runtime():
    """获取或创建新工具运行时（registry + dispatcher + resolver）。"""
    global _tool_runtime, _tool_runtime_hash
    current_hash = _tool_fingerprint()
    if _tool_runtime is None or _tool_runtime_hash != current_hash:
        from backend.modules.agent.tools.adapters import PydanticAIToolAdapter
        from backend.modules.agent.tools.core.factory import create_tool_runtime

        registry, dispatcher, resolver = create_tool_runtime()
        adapter = PydanticAIToolAdapter(registry, dispatcher, resolver)
        _tool_runtime = (registry, dispatcher, resolver, adapter)
        _tool_runtime_hash = current_hash
    return _tool_runtime


def _create_model() -> OpenAIChatModel:
    """创建 LiteLLM 兼容的 OpenAI 模型实例
    Raises:
        ValueError: 如果未配置 API Key
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    settings = get_settings()

    provider = settings.llm_provider
    model_name = settings.llm_model
    api_key = settings.llm_api_key or settings.dashscope_api_key
    base_url = settings.llm_base_url

    # 检查 API Key 是否配置
    if not api_key:
        raise ValueError(
            "未配置 LLM API Key。请在设置页面配置 API Key，或在 .env 文件中设置 DASHSCOPE_API_KEY 或 LLM_API_KEY。"
        )

    if not base_url:
        raise ValueError(
            f"未配置 LLM Base URL。请在设置页面配置 Base URL，"
            f"或在 .env 文件中设置 LLM_BASE_URL（当前 provider: {provider}）。"
        )

    logger.info("创建模型", provider=provider, model=model_name, base_url=base_url, has_key=bool(api_key))

    model_id = model_name

    return OpenAIChatModel(
        model_id,
        provider=OpenAIProvider(
            base_url=base_url,
            api_key=api_key,
        ),
    )


def create_agent() -> Agent[LumenDeps, str]:
    """创建 Lumen Agent 实例

    Returns:
        配置好的 PydanticAI Agent
    """
    from pydantic_ai import Agent, RunContext

    model = _create_model()

    # 新工具运行时（Registry + Dispatcher + Adapter）
    _, _, _, adapter = _get_tool_runtime()
    all_toolsets = [adapter.build_toolset(["default-chat"])]

    agent = Agent(
        model=model,
        deps_type=LumenDeps,
        output_type=str,
        system_prompt=(
            "你是「Lumen」，用户的 AI 伴侣。说话像一个真正认识你的朋友，不是客服，不奉承。\n\n"
            "用户分享个人信息时立即用 update_profile / memory_save 保存；"
            "需要回忆时用 memory_search；搜外部笔记用 scope='knowledge'。"
            "搜不到如实说，别编；搜完空结果也要告诉用户'没找到相关内容'，不要沉默。\n"
            "调用工具前先说你在做什么（哪怕一句），别闷声执行。\n\n"
            "开场白简短自然，不罗列功能。回复直接开始，不要以逗号或其他标点符号打头。"
        ),
        retries=2,
        end_strategy="graceful",  # 流式 output_type=str：同时返回文本+工具调用时仍需执行工具
        toolsets=all_toolsets,
    )

    # 动态系统提示词：记忆上下文 + 对话历史（放在 system prompt 中而非用户消息）
    # 语义上正确：上下文是系统级背景信息，模型能区分「指令+背景」和「用户请求」
    @agent.system_prompt
    async def dynamic_prompt(ctx: RunContext[LumenDeps]) -> str:
        # ── 对话摘要（传入 build_context 统一包裹）──
        conversation_summary: str | None = None
        try:
            conv = await ctx.deps.db.get(Conversation, ctx.deps.conversation_id)
            if conv and conv.summary:
                conversation_summary = conv.summary
        except Exception:
            pass

        # ── 结构化画像 + 语义召回 + 对话摘要 ──
        memory_instance = get_memory()
        context = await memory_instance.build_context(
            ctx.deps.user_id,
            user_input=ctx.deps.current_user_input,
            conversation_summary=conversation_summary,
        )
        if context.strip():
            ctx.deps.build_context_cache = context
            return context

        return "【用户画像为空】用户尚未提供个人信息。当用户提供信息时，调用 memory_save 或 update_profile 保存。"

    return agent


def _config_fingerprint() -> str:
    """计算完整配置指纹（LLM + 工具配置），用于判断是否需要重建 Agent。"""
    s = get_settings()
    raw = f"{s.llm_provider}|{s.llm_model}|{s.llm_api_key or s.dashscope_api_key}|{s.llm_base_url}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _tool_fingerprint() -> str:
    """计算工具配置指纹，用于判断是否需要重建工具运行时。"""
    return "v1"


def get_agent() -> Agent[LumenDeps, str]:
    """获取 Agent 实例（config hash 不变时复用缓存，减少重复注册开销）。"""
    global _cached_agent, _cached_config_hash, _cached_agent_generation
    fp = _config_fingerprint()
    if _cached_agent is not None and _cached_config_hash == fp:
        return _cached_agent
    _cached_agent = create_agent()
    _cached_config_hash = fp
    _cached_agent_generation += 1
    logger.info("Agent 已重建", generation=_cached_agent_generation)
    return _cached_agent


def get_agent_generation() -> int:
    """返回当前 Agent 代际号，用于请求执行期间检测 Agent 是否被重建。"""
    return _cached_agent_generation
