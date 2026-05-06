"""PydanticAI Agent 定义 — CareerOS 职业规划助手"""

from __future__ import annotations

import hashlib
import logging

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from app.backend.agent.deps import CareerOSDeps
from app.backend.config import get_settings

logger = logging.getLogger(__name__)

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

    # DashScope OpenAI 兼容端点
    if provider == "dashscope" and not base_url:
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

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
            "你是 CareerOS，一个面向中国 CS 学生的 AI 职业规划助手。"
            "你的目标是帮助学生从大一到毕业，追踪成长轨迹，提供个性化的职业规划建议。"
            "\n\n"
            "## 核心能力\n"
            "1. 结合用户画像，对用户提供的岗位描述（JD）与求职方向做分析与建议（无独立结构化诊断 API）\n"
            "2. 提供学习路径和职业发展建议\n"
            "3. 追踪成长里程碑\n"
            "\n\n"
            "## 用户画像规则\n"
            "用户画像（学校、专业、年级、技能、目标）已自动加载到上下文中的「用户信息」部分。\n"
            "- 【不要】主动调用 get_profile 工具，画像已在上下文中\n"
            "- 当用户提到个人信息（学校、专业、年级、目标方向等）时，【必须】调用 memory_save 工具保存\n"
            "- 保存后告诉用户「已记录你的信息」，不要说「已同步」而不实际调用工具\n"
            "\n\n"
            "## 回复风格\n"
            "- 使用中文\n"
            "- 结构化输出（使用 Markdown）\n"
            "- 给出具体可执行的建议\n"
            "- 鼓励而非说教\n"
            "- 不要重复询问用户已经说过的信息\n"
            "\n\n"
            "## 自我介绍规范\n"
            "- 你是一个通用的职业规划助手，不要说「专为XX定制」或「专为XX设计」\n"
            "- 首次对话时，简短欢迎即可（1-2句），不要长篇大论\n"
            "- 不要把用户的画像信息全部复述一遍，用户自己知道自己的信息\n"
            "- 直接询问用户需要什么帮助，而不是列出一堆「你需要补充的信息」"
        ),
        retries=2,
    )

    # 注册工具
    register_tools(agent)

    # 注册动态系统提示词
    @agent.system_prompt
    async def dynamic_prompt(ctx: RunContext[CareerOSDeps]) -> str:
        """动态系统提示词：注入 3 个记忆文件 + 对话摘要 + 近期历史"""
        from sqlalchemy import select

        from app.backend.models.conversation import Conversation, Message

        db = ctx.deps.db
        parts = []

        # ── 结构化画像 + 语义召回 ──────────────────────
        from app.backend.services.careeros_memory import get_memory

        memory = get_memory()
        context = await memory.build_context(ctx.deps.user_id, user_input=ctx.deps.current_user_input)
        if context.strip():
            parts.append(context)

        # ── 对话摘要（超过 30 条消息后由后台生成）──────────────
        try:
            conv = await db.get(Conversation, ctx.deps.conversation_id)
            if conv and conv.summary:
                parts.append(f"【对话摘要】\n{conv.summary}")
        except Exception:
            pass

        # ── 近期对话历史（最近 20 条）─────────────────────────
        try:
            history_result = await db.execute(
                select(Message)
                .where(Message.conversation_id == ctx.deps.conversation_id)
                .order_by(Message.created_at.desc())
                .limit(20)
            )
            history_messages = list(reversed(history_result.scalars().all()))

            # 去掉最后一条 user 消息（避免与当前 user_input 重复）
            if history_messages and history_messages[-1].role == "user":
                history_messages = history_messages[:-1]

            if history_messages:
                history_lines = []
                for msg in history_messages:
                    if msg.role == "user":
                        history_lines.append(f"用户：{msg.content}")
                    elif msg.role == "assistant":
                        content = (msg.content or "")[:200]
                        history_lines.append(f"AI：{content}")
                parts.append("【对话历史】\n" + "\n".join(history_lines))
        except Exception:
            pass

        if parts:
            return "\n\n".join(parts)
        return "【用户画像为空】用户尚未填写个人信息。当用户提供信息时，调用 memory_update 保存。"

    logger.info("PydanticAI Agent created with model: %s", model.model_name)
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
