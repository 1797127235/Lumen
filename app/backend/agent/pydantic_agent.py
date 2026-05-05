"""PydanticAI Agent 定义 — CareerOS 职业规划助手"""

from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.backend.agent.deps import CareerOSDeps
from app.backend.agent.pydantic_tools import register_tools
from app.backend.config import get_settings

logger = logging.getLogger(__name__)


def _create_model() -> OpenAIChatModel:
    """创建 LiteLLM 兼容的 OpenAI 模型实例

    Raises:
        ValueError: 如果未配置 API Key
    """
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
    model = _create_model()

    agent = Agent(
        model=model,
        deps_type=CareerOSDeps,
        output_type=str,
        system_prompt=(
            "你是 CareerOS，一个面向中国 CS 学生的 AI 职业规划助手。"
            "你的目标是帮助学生从大一到毕业，追踪成长轨迹，提供个性化的职业规划建议。"
            "\n\n"
            "核心能力："
            "1. 分析用户画像（学校、专业、技能、目标）"
            "2. 诊断 JD 匹配度，找出技能缺口"
            "3. 提供学习路径和职业发展建议"
            "4. 追踪成长里程碑"
            "\n\n"
            "回复风格："
            "- 使用中文"
            "- 结构化输出（使用 Markdown）"
            "- 给出具体可执行的建议"
            "- 鼓励而非说教"
        ),
        retries=2,
    )

    # 注册工具
    register_tools(agent)

    # 注册动态系统提示词
    @agent.system_prompt
    async def dynamic_prompt(ctx: RunContext[CareerOSDeps]) -> str:
        """动态系统提示词：加载用户画像和记忆"""
        from sqlalchemy import select

        from app.backend.models.user import User, UserProfile
        from app.backend.services import cognee_service

        db = ctx.deps.db
        user_id = ctx.deps.user_id

        parts = []

        # 加载用户画像
        result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        profile = result.scalar_one_or_none()

        if profile:
            user = await db.get(User, user_id)
            nickname = user.nickname if user else None

            if nickname:
                parts.append(f"用户昵称：{nickname}")
            if profile.school_name:
                parts.append(f"学校：{profile.school_name}")
            if profile.major:
                parts.append(f"专业：{profile.major}")
            if profile.grade:
                parts.append(f"年级：{profile.grade}")
            if profile.target_direction:
                parts.append(f"目标方向：{profile.target_direction}")

        # 加载相关记忆
        try:
            memories = await cognee_service.recall(user_id, "职业规划技能成长", limit=3)
            if memories:
                parts.append("相关记忆：")
                for mem in memories:
                    parts.append(f"- {mem}")
        except Exception:
            pass  # 记忆加载失败不影响对话

        if parts:
            return "用户信息：\n" + "\n".join(parts)
        return ""

    logger.info("PydanticAI Agent created with model: %s", model.model_name)
    return agent


def get_agent() -> Agent[CareerOSDeps, str]:
    """获取 Agent 实例（每次创建新实例，避免配置过期）
    注意：不使用单例模式，因为用户可能在运行时更改 LLM 配置。
    Agent 创建是轻量级操作，不影响性能。
    """
    return create_agent()
