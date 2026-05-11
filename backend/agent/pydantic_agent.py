"""PydanticAI Agent 定义 — Lumen"""

from __future__ import annotations

import hashlib

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from backend.agent.deps import LumenDeps
from backend.config import get_settings
from backend.domain.models import Conversation
from backend.logging_config import get_logger
from backend.memory import get_memory

logger = get_logger(__name__)

# Agent 缓存：config hash 不变时复用实例，避免每次重新注册 tools/dynamic_prompt
# _cached_agent_generation：每次重建递增，用于追踪请求是否使用陈旧 Agent
_cached_agent: Agent[LumenDeps, str] | None = None
_cached_config_hash: str = ""
_cached_agent_generation: int = 0


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

    from backend.agent.tools import register_all_tools

    model = _create_model()

    agent = Agent(
        model=model,
        deps_type=LumenDeps,
        output_type=str,
        system_prompt=(
            "你是「Lumen」，用户的 AI 伴侣。性格：深谋远虑但平易近人，说话像一个真正认识你的朋友，"
            "不是客服，不奉承，有时候会说实话，包括用户不想听的。\n\n"
            "规则：用户提到以下信息时必须立即调用工具保存。\n"
            "职业方向/目标岗位→update_profile(target_direction=...) | 学校/专业/年级→update_profile()\n"
            "技能→memory_save('skills',名称,程度) | 经历→memory_save('experiences',标题,描述)\n"
            "偏好→memory_save('preferences',名,内容) | 具体有时限的目标→memory_save('goals',标题,计划)\n"
            "先保存再回答，一句话告知，不要只回「已记录」。\n\n"
            "memory_search scope 选择：问技能/经历/画像→profile；问情绪/焦虑/内心→emotions；"
            "问公司/行业/学长→reference；问历史对话→chat；跨领域或不确定→不传 scope。\n"
            "memory_search search_mode 选择：\n"
            "  具体关键词（「Python」「实习」「项目」）→ search_mode='keyword'\n"
            "  时间范围（「最近做了什么」「这周」「这几天」）→ search_mode='grep' + time_filter='recent_7d'\n"
            "调用任何工具后必须生成文字回复，不能以工具调用结束对话；"
            "memory_search 搜到内容时把结果告诉用户，搜不到时说明并给出建议。\n\n"
            "开场白：简短自然，不罗列功能，不问「有什么可以帮您」。"
            "示例：「我是 Lumen。你在哪个阶段，就从哪里说起。」"
        ),
        retries=2,
        end_strategy="graceful",  # 流式 output_type=str：同时返回文本+工具调用时仍需执行工具
    )

    # 注册工具
    register_all_tools(agent)

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

        return "【用户画像为空】用户尚未填写个人信息。当用户提供信息时，调用 memory_save 或 update_profile 保存。"

    return agent


def _config_fingerprint() -> str:
    """计算 LLM 配置指纹，用于判断是否需要重建 Agent。"""
    s = get_settings()
    raw = f"{s.llm_provider}|{s.llm_model}|{s.llm_api_key or s.dashscope_api_key}|{s.llm_base_url}"
    return hashlib.sha256(raw.encode()).hexdigest()


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
