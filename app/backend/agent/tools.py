"""工具注册中心 — Agent 可调用的外部工具"""

from __future__ import annotations

from typing import Any


async def knowledge_search(query: str) -> list[dict[str, Any]]:
    """知识库检索"""
    from app.backend.agent.rag import get_rag

    return await get_rag().search(query)


async def generate_learning_path(params: dict) -> dict:
    """学习路径生成（M-02）"""
    # MVP: 调用 LLM 生成，后续独立路径引擎
    from app.backend.agent.llm_router import chat

    prompt = f"""你是一个学习路径规划专家。请根据以下信息生成定制化的学习路径：
目标岗位：{params.get("target_role")}
当前基础：{params.get("current_level")}
每日可用时间：{params.get("daily_time_hours")}小时
目标公司：{params.get("target_company")}

请返回 JSON 格式的路径，每个节点包含：node_name, description, estimated_hours, acceptance_criteria"""
    result = await chat("path_generation", [{"role": "user", "content": prompt}])
    return {"raw": result}


# ── 工具注册表 ──
TOOL_REGISTRY: dict[str, Any] = {
    "knowledge_search": {
        "name": "knowledge_search",
        "description": "检索职业规划知识库，获取方向介绍、岗位信息、学习建议等",
        "fn": knowledge_search,
    },
    "generate_learning_path": {
        "name": "generate_learning_path",
        "description": "根据用户目标岗位和当前基础，生成个性化学习路径",
        "fn": generate_learning_path,
    },
}
