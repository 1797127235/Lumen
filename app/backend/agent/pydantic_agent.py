"""PydanticAI Agent 定义 — CareerOS 职业规划助手"""

from __future__ import annotations

import hashlib

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from app.backend.agent.deps import CareerOSDeps
from app.backend.config import get_settings
from app.backend.logging_config import get_logger

logger = get_logger(__name__)

# Agent 缓存：config hash 不变时复用实例，避免每次重新注册 tools/dynamic_prompt
_cached_agent: Agent[CareerOSDeps, str] | None = None
_cached_config_hash: str = ""


def _create_model() -> OpenAIChatModel:
    """创建 LiteLLM 兼容的 OpenAI 模型实例

    Raises:
        ValueError: 如果未配置 API Key
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    settings = get_settings()

    provider = settings.llm_provider or "dashscope"
    model_name = settings.llm_model or "qwen-plus"
    api_key = settings.llm_api_key or settings.dashscope_api_key
    base_url = settings.llm_base_url

    # 检查 API Key 是否配置
    if not api_key:
        raise ValueError(
            "未配置 LLM API Key。请在设置页面配置 API Key，或在 .env 文件中设置 DASHSCOPE_API_KEY 或 LLM_API_KEY。"
        )

    # DeepSeek OpenAI 兼容端点
    if provider == "deepseek" and not base_url:
        base_url = "https://api.deepseek.com"

    # DashScope OpenAI 兼容端点
    if provider == "dashscope" and not base_url:
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    if not base_url:
        raise ValueError(
            f"未配置 LLM Base URL。请在设置页面配置 Base URL，"
            f"或在 .env 文件中设置 LLM_BASE_URL（当前 provider: {provider}）。"
        )

    logger.info("创建模型", provider=provider, model=model_name, base_url=base_url, has_key=bool(api_key))

    # DashScope OpenAI 兼容端点
    if provider == "dashscope" and not base_url:
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # DeepSeek OpenAI 兼容端点
    if provider == "deepseek" and not base_url:
        base_url = "https://api.deepseek.com"

    if not base_url:
        raise ValueError(
            f"未配置 LLM Base URL。请在设置页面配置 Base URL，"
            f"或在 .env 文件中设置 LLM_BASE_URL（当前 provider: {provider}）。"
        )

    # PydanticAI OpenAI Provider 使用纯模型名（不带 provider 前缀）
    model_id = model_name

    return OpenAIChatModel(
        model_id,
        provider=OpenAIProvider(
            base_url=base_url,
            api_key=api_key,
        ),
    )


def create_agent() -> Agent[CareerOSDeps, str]:
    """创建 CareerOS Agent 实例

    Returns:
        配置好的 PydanticAI Agent
    """
    from pydantic_ai import Agent, RunContext

    from app.backend.agent.pydantic_tools import register_tools

    model = _create_model()

    agent = Agent(
        model=model,
        deps_type=CareerOSDeps,
        output_type=str,
        system_prompt=(
            "你是 CareerOS。规则：用户提到职业目标/技能/经历/学校时必须调用工具保存。\n"
            "目标→memory_save('goals',方向,动机) | 技能→memory_save('skills',名称,程度)\n"
            "经历→memory_save('experiences',标题,描述) | 学校→update_profile() | 偏好→memory_save('preferences',名,内容)\n"
            "先保存再回答，一句话告知，不要只回「已记录」。"
        ),
        retries=2,
        end_strategy="graceful",  # 流式 output_type=str：同时返回文本+工具调用时仍需执行工具
    )

    # 注册工具
    register_tools(agent)

    # 动态系统提示词：记忆上下文 + 对话历史（放在 system prompt 中而非用户消息）
    # 语义上正确：上下文是系统级背景信息，模型能区分「指令+背景」和「用户请求」
    @agent.system_prompt
    async def dynamic_prompt(ctx: RunContext[CareerOSDeps]) -> str:
        from sqlalchemy import select

        from app.backend.models.conversation import Conversation, Message

        db = ctx.deps.db
        parts = []

        # ── 结构化画像 + 语义召回 ──
        from app.backend.services.careeros_memory import get_memory

        memory = get_memory()
        context = await memory.build_context(ctx.deps.user_id, user_input=ctx.deps.current_user_input)
        if context.strip():
            ctx.deps.build_context_cache = context
            parts.append(context)

        # ── 对话摘要 ──
        try:
            conv = await db.get(Conversation, ctx.deps.conversation_id)
            if conv and conv.summary:
                parts.append(f"【对话摘要】\n{conv.summary}")
        except Exception:
            pass

        # ── 近期对话历史（最近 10 条）──
        try:
            history_result = await db.execute(
                select(Message)
                .where(Message.conversation_id == ctx.deps.conversation_id)
                .order_by(Message.created_at.desc())
                .limit(10)
            )
            history = list(history_result.scalars().all())
            history.reverse()
            if history:
                lines = ["【近期对话】"]
                for msg in history:
                    tag = "用户" if msg.role == "user" else "助手"
                    lines.append(f"{tag}: {(msg.content or '')[:150]}")
                parts.append("\n".join(lines))
        except Exception:
            pass

        if not parts:
            return "【用户画像为空】用户尚未填写个人信息。当用户提供信息时，调用 memory_save 或 update_profile 保存。"

        return "\n\n".join(parts)

    return agent


def _config_fingerprint() -> str:
    """计算 LLM 配置指纹，用于判断是否需要重建 Agent。"""
    s = get_settings()
    raw = f"{s.llm_provider}|{s.llm_model}|{s.llm_api_key or s.dashscope_api_key}|{s.llm_base_url}"
    return hashlib.sha256(raw.encode()).hexdigest()


def get_agent() -> Agent[CareerOSDeps, str]:
    """获取 Agent 实例（config hash 不变时复用缓存，减少重复注册开销）。"""
    global _cached_agent, _cached_config_hash
    fp = _config_fingerprint()
    if _cached_agent is not None and _cached_config_hash == fp:
        return _cached_agent
    _cached_agent = create_agent()
    _cached_config_hash = fp
    return _cached_agent
