"""LLM 路由 — 按用途自动选择模型"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from openai import AsyncOpenAI

from app.backend.config import get_settings

# ── 任务类型 → 模型映射 ──
# 不设 LLM_MODEL 环境变量，代码内置路由
_ROUTE_MAP: dict[str, str] = {
    "general_chat": "qwen-plus",        # 日常对话、通用问答
    "career_planning": "qwen-plus",      # 职业规划、路径生成（需强推理）
    "resume_optimize": "qwen-plus",      # 简历优化（需结构化输出）
    "mock_interview": "qwen-plus",       # 模拟面试（需追问逻辑）
    "skill_analysis": "qwen-plus",      # 技能分析（轻量）
    "path_generation": "qwen-plus",      # 路径生成（复杂）
    "memory_summarize": "qwen-plus",    # 记忆摘要（轻量）
    "embedding": "text-embedding-v4",   # 向量化（专用模型）
}

TaskType = Literal[
    "general_chat", "career_planning", "resume_optimize",
    "mock_interview", "skill_analysis", "path_generation",
    "memory_summarize", "embedding",
]


@lru_cache()
def _get_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
    )


def get_model(task_type: TaskType) -> str:
    return _ROUTE_MAP.get(task_type, "qwen-plus")


async def chat_stream(
    task_type: TaskType,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2048,
):
    """流式调用 LLM，返回 token 迭代器"""
    client = _get_client()
    model = get_model(task_type)
    # 事实类降低温度，创意类用默认
    temp = 0.3 if task_type in ("skill_analysis", "memory_summarize") else temperature
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temp,
        max_tokens=max_tokens,
        stream=True,
    )
    async for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content


async def chat(
    task_type: TaskType,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """非流式调用 LLM，返回完整文本"""
    client = _get_client()
    model = get_model(task_type)
    temp = 0.3 if task_type in ("skill_analysis", "memory_summarize") else temperature
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temp,
        max_tokens=max_tokens,
        stream=False,
    )
    return response.choices[0].message.content or ""


async def embed(text: str) -> list[float]:
    """文本向量化"""
    client = _get_client()
    resp = await client.embeddings.create(
        model=_ROUTE_MAP["embedding"],
        input=text,
    )
    return resp.data[0].embedding
