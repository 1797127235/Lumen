"""Agent 工具注册入口。

各工具分别在独立模块中定义，通过此文件统一注册到 Agent。
新工具加新文件 + 在此 register_all_tools 中注册即可。
"""

from __future__ import annotations

from pydantic_ai import Agent

from app.backend.agent.deps import LumenDeps


def register_all_tools(agent: Agent[LumenDeps, str]) -> None:
    """注册所有 Agent 工具。"""
    from app.backend.agent.tools import memory_save, memory_search, profile

    memory_search.register(agent)
    memory_save.register(agent)
    profile.register(agent)
