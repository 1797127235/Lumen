"""Agent 工具定义 — PydanticAI 版本

注意：此文件保留用于向后兼容和文档目的。
实际工具注册使用 pydantic_tools.py 中的 @agent.tool 装饰器。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 工具列表（用于文档和测试）
TOOLS = [
    {
        "name": "get_profile",
        "description": "读取用户画像，包括学校、专业、技能、目标方向等信息。当需要了解用户背景时调用。",
    },
    {
        "name": "update_profile",
        "description": "从对话中增量更新用户画像。当用户提到目标方向、目标公司、个人偏好等信息时调用。",
    },
    {
        "name": "diagnose_jd",
        "description": "诊断用户与 JD 的匹配度。当用户粘贴 JD 或询问岗位匹配情况时调用。",
    },
]
