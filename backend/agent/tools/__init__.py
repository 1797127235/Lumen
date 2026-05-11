"""Agent 工具模块。"""

from pydantic_ai import Agent

from backend.agent.deps import LumenDeps
from backend.agent.tools.tool_memory_save import register as register_memory_save
from backend.agent.tools.tool_memory_search import register as register_memory_search
from backend.agent.tools.tool_profile import register as register_profile


def register_all_tools(agent: Agent[LumenDeps, str]) -> None:
    """注册所有 Agent 工具。"""
    register_memory_search(agent)
    register_memory_save(agent)
    register_profile(agent)


__all__ = [
    "register_all_tools",
    "register_memory_search",
    "register_memory_save",
    "register_profile",
]
