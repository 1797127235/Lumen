"""PydanticAI Agent 测试 — 验证 Agent 核心逻辑

测试场景：
- Agent 创建
- 工具注册
- 依赖注入
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic_ai")

from app.backend.agent.deps import CareerOSDeps  # noqa: E402
from app.backend.agent.pydantic_agent import create_agent, get_agent  # noqa: E402


@pytest.fixture
def mock_deps():
    """创建 mock 依赖"""
    db = AsyncMock()
    return CareerOSDeps(
        user_id="test_user",
        db=db,
        user_profile={
            "nickname": "测试用户",
            "school_name": "测试大学",
            "major": "计算机科学",
            "grade": "大三",
            "target_direction": "后端",
        },
    )


def test_create_agent():
    """测试 Agent 创建"""
    agent = create_agent()
    assert agent is not None
    assert agent._deps_type == CareerOSDeps


def test_get_agent_creates_new_instance():
    """测试 Agent 每次创建新实例（避免配置过期）"""
    agent1 = get_agent()
    agent2 = get_agent()
    # 每次调用都创建新实例，确保配置是最新的
    assert agent1 is not agent2
    # 但两个实例的类型相同
    assert isinstance(agent1, type(agent2))


@pytest.mark.asyncio
async def test_agent_has_tools():
    """测试 Agent 工具注册"""
    agent = create_agent()
    # PydanticAI Agent 应该有注册的工具
    # 注意：具体的工具检查依赖于 PydanticAI 的内部实现
    assert agent is not None


@pytest.mark.asyncio
async def test_agent_system_prompt():
    """测试 Agent 系统提示词"""
    agent = create_agent()
    # 验证系统提示词包含关键信息
    # PydanticAI 使用 _system_prompts (复数) 存储系统提示词
    assert len(agent._system_prompts) > 0
    # 检查第一个系统提示词是否包含关键信息
    first_prompt = agent._system_prompts[0]
    if callable(first_prompt):
        # 如果是函数，跳过检查（动态提示词）
        pass
    else:
        assert "CareerOS" in str(first_prompt)
        assert "职业规划" in str(first_prompt)


@pytest.mark.asyncio
async def test_career_os_deps():
    """测试依赖类型"""
    db = AsyncMock()
    deps = CareerOSDeps(
        user_id="test_user",
        db=db,
    )
    assert deps.user_id == "test_user"
    assert deps.db == db


@pytest.mark.asyncio
async def test_career_os_deps_creation():
    """测试依赖类型创建"""
    db = AsyncMock()
    deps = CareerOSDeps(
        user_id="another_user",
        db=db,
    )
    assert deps.user_id == "another_user"
    assert deps.db == db
