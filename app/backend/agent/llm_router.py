"""LLM 路由 — 按用途自动选择模型（LiteLLM 统一抽象层）"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

import litellm

from app.backend.config import get_settings

logger = logging.getLogger(__name__)

# LiteLLM 全局配置
litellm.drop_params = True  # 丢弃不支持的参数，避免报错
litellm.modify_params = True  # 自动修改参数

# ── 任务类型 → 模型映射（不含 provider 前缀）──
_ROUTE_MAP: dict[str, str] = {
    "general_chat": "qwen-plus",  # 日常对话、通用问答
    "career_planning": "qwen-plus",  # 职业规划、路径生成（需强推理）
    "resume_optimize": "qwen-plus",  # 简历优化（需结构化输出）
    "mock_interview": "qwen-plus",  # 模拟面试（需追问逻辑）
    "skill_analysis": "qwen-plus",  # 技能分析（轻量）
    "path_generation": "qwen-plus",  # 路径生成（复杂）
    "memory_summarize": "qwen-plus",  # 记忆摘要（轻量）
    "embedding": "text-embedding-v4",  # 向量化（专用模型）
}

TaskType = Literal[
    "general_chat",
    "career_planning",
    "resume_optimize",
    "mock_interview",
    "skill_analysis",
    "path_generation",
    "memory_summarize",
    "embedding",
]


def _get_model_identifier(task_type: TaskType) -> str:
    """返回 LiteLLM 格式的 model identifier：provider/model"""
    settings = get_settings()
    if task_type == "embedding":
        provider = settings.embedding_provider or settings.llm_provider or "dashscope"
        model = settings.embedding_model or _ROUTE_MAP.get(task_type, "text-embedding-v4")
    else:
        provider = settings.llm_provider or "dashscope"
        model = settings.llm_model or _ROUTE_MAP.get(task_type, "qwen-plus")

    # OpenAI 不需要 provider 前缀
    if provider == "openai":
        return model
    return f"{provider}/{model}"


def _get_api_key(for_embedding: bool = False) -> str:
    """获取 API Key — 专用 key → LLM key（无 fallback 到 dashscope_api_key）"""
    settings = get_settings()
    if for_embedding:
        return settings.embedding_api_key or settings.llm_api_key
    return settings.llm_api_key


def _get_base_url(for_embedding: bool = False) -> str | None:
    """获取 base_url，空字符串返回 None（让 LiteLLM 用默认值）"""
    settings = get_settings()
    url = settings.embedding_base_url if for_embedding else settings.llm_base_url
    return url or None


async def chat_stream(
    task_type: TaskType,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2048,
    retries: int = 2,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
):
    """流式调用 LLM，返回 token 迭代器（LiteLLM，内置重试）"""
    model_id = model or _get_model_identifier(task_type)
    key = api_key or _get_api_key()
    url = base_url if base_url is not None else _get_base_url()
    temp = 0.3 if task_type in ("skill_analysis", "memory_summarize") else temperature

    for attempt in range(retries + 1):
        try:
            kwargs: dict = {
                "model": model_id,
                "messages": messages,
                "temperature": temp,
                "max_tokens": max_tokens,
                "api_key": key,
                "stream": True,
                "timeout": 60,
            }
            if url:
                kwargs["base_url"] = url

            response = await litellm.acompletion(**kwargs)
            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield delta.content
            return
        except Exception as e:
            if attempt < retries:
                logger.warning("LLM stream failed (attempt %d/%d): %s", attempt + 1, retries + 1, e)
                await asyncio.sleep(2**attempt)
                continue
            logger.error("LLM stream failed after %d retries: %s", retries + 1, e)
            raise


async def chat(
    task_type: TaskType,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2048,
    retries: int = 2,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> str:
    """非流式调用 LLM，返回完整文本（LiteLLM，内置重试）"""
    model_id = model or _get_model_identifier(task_type)
    key = api_key or _get_api_key()
    url = base_url if base_url is not None else _get_base_url()
    temp = 0.3 if task_type in ("skill_analysis", "memory_summarize") else temperature

    for attempt in range(retries + 1):
        try:
            kwargs: dict = {
                "model": model_id,
                "messages": messages,
                "temperature": temp,
                "max_tokens": max_tokens,
                "api_key": key,
                "stream": False,
                "timeout": 60,
            }
            if url:
                kwargs["base_url"] = url

            response = await litellm.acompletion(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            if attempt < retries:
                logger.warning("LLM chat failed (attempt %d/%d): %s", attempt + 1, retries + 1, e)
                await asyncio.sleep(2**attempt)
                continue
            logger.error("LLM chat failed after %d retries: %s", retries + 1, e)
            raise


async def embed(text: str) -> list[float]:
    """文本向量化（LiteLLM）"""
    model_id = _get_model_identifier("embedding")
    key = _get_api_key(for_embedding=True)
    url = _get_base_url(for_embedding=True)

    kwargs: dict = {
        "model": model_id,
        "input": text,
        "api_key": key,
    }
    if url:
        kwargs["base_url"] = url

    resp = await litellm.aembedding(**kwargs)
    if not resp.data:
        raise ValueError("Embedding API returned empty data")
    return resp.data[0].embedding
