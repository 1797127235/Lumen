"""PydanticAI Agent 定义 — CareerOS 职业规划助手"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.backend.agent.deps import CareerOSDeps
from app.backend.config import get_settings

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel

logger = logging.getLogger(__name__)


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
            "1. 诊断 JD 匹配度，找出技能缺口\n"
            "2. 提供学习路径和职业发展建议\n"
            "3. 追踪成长里程碑\n"
            "\n\n"
            "## 用户画像规则\n"
            "用户画像（学校、专业、年级、技能、目标）已自动加载到上下文中的「用户信息」部分。\n"
            "- 【不要】主动调用 get_profile 工具，画像已在上下文中\n"
            "- 当用户提到个人信息（学校、专业、年级、目标方向等）时，【必须】调用 update_profile 工具保存\n"
            "- 保存后告诉用户「已记录你的信息」，不要说「已同步」而不实际调用工具\n"
            "\n\n"
            "## 回复风格\n"
            "- 使用中文\n"
            "- 结构化输出（使用 Markdown）\n"
            "- 给出具体可执行的建议\n"
            "- 鼓励而非说教\n"
            "- 不要重复询问用户已经说过的信息"
        ),
        retries=2,
    )

    # 注册工具
    register_tools(agent)

    # 注册动态系统提示词
    @agent.system_prompt
    async def dynamic_prompt(ctx: RunContext[CareerOSDeps]) -> str:
        """动态系统提示词：加载用户画像、记忆和历史消息"""
        from sqlalchemy import select

        from app.backend.models.conversation import Message
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
                grade_map = {
                    "freshman": "大一",
                    "sophomore": "大二",
                    "junior": "大三",
                    "senior": "大四",
                    "graduate1": "研一",
                    "graduate2": "研二",
                    "graduate3": "研三",
                }
                parts.append(f"年级：{grade_map.get(profile.grade, profile.grade)}")
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

        # 加载历史消息（L1 短期记忆）
        try:
            # 从 DB 加载最近 20 条消息（按 created_at 倒序）
            history_result = await db.execute(
                select(Message)
                .where(Message.conversation_id == ctx.deps.conversation_id)
                .order_by(Message.created_at.desc())
                .limit(20)
            )
            history_messages = history_result.scalars().all()

            if history_messages:
                # 反转为正序（最早的在前）
                history_messages = list(reversed(history_messages))

                # 格式化为文本
                history_lines = []
                for msg in history_messages:
                    if msg.role == "user":
                        history_lines.append(f"用户：{msg.content}")
                    elif msg.role == "assistant":
                        # 截断过长的回复，保留前 200 字符
                        content = msg.content or ""
                        if len(content) > 200:
                            content = content[:200] + "..."
                        history_lines.append(f"AI：{content}")

                if history_lines:
                    parts.append("【对话历史】\n" + "\n".join(history_lines))
        except Exception:
            pass  # 历史消息加载失败不影响对话

        if parts:
            return (
                "【用户画像 - 已自动加载，不要调用 get_profile】\n"
                + "\n".join(parts)
                + "\n\n注意：以上信息已加载，直接使用。当用户提到新信息时，调用 update_profile 保存。"
            )
        return "【用户画像为空】用户尚未填写个人信息。当用户提供信息时，调用 update_profile 保存。"

    logger.info("PydanticAI Agent created with model: %s", model.model_name)
    return agent


def get_agent() -> Agent[CareerOSDeps, str]:
    """获取 Agent 实例（每次创建新实例，避免配置过期）
    注意：不使用单例模式，因为用户可能在运行时更改 LLM 配置。
    Agent 创建是轻量级操作，不影响性能。
    """
    return create_agent()
