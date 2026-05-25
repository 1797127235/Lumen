"""测试新工具层 — memory_save / memory_search / get_profile / update_profile。"""

from __future__ import annotations

import pytest

from lib.memory.markdown import AsyncMarkdownStore
from lib.tools.memory import create_memory_tools
from lib.tools.profile import create_profile_tools


class FakeDeps:
    def __init__(self, user_id: str = "test_tools_user"):
        self.user_id = user_id


class TestMemoryTools:
    @pytest.fixture
    def store(self):
        return AsyncMarkdownStore()

    @pytest.fixture
    def deps(self):
        return FakeDeps("test_tools_user")

    @pytest.fixture
    def memory_tools(self):
        tools = {t.name: t for t in create_memory_tools()}
        return tools

    @pytest.mark.asyncio
    async def test_memory_save_writes_md(self, memory_tools, deps, store):
        save_tool = memory_tools["memory_save"]
        result = await save_tool.execute(
            {"entity_type": "preferences", "section": "风格", "content": "用户偏好直接回答"},
            deps,
        )
        assert "已记录" in result

        content = await store.read_memory(deps.user_id)
        assert "用户偏好直接回答" in content
        assert "[preferences]" in content

    @pytest.mark.asyncio
    async def test_memory_search_finds_content(self, memory_tools, deps, store):
        # 先写入
        await store.append_memory_entry(deps.user_id, "work", "用户在写 FastAPI 项目")

        search_tool = memory_tools["memory_search"]
        result = await search_tool.execute({"query": "FastAPI"}, deps)
        assert "FastAPI" in result

    @pytest.mark.asyncio
    async def test_memory_search_empty(self, memory_tools, deps):
        search_tool = memory_tools["memory_search"]
        result = await search_tool.execute({"query": "不存在的词"}, deps)
        assert "未找到" in result


class TestProfileTools:
    @pytest.fixture
    def store(self):
        return AsyncMarkdownStore()

    @pytest.fixture
    def deps(self):
        return FakeDeps("test_profile_user")

    @pytest.fixture
    def profile_tools(self):
        tools = {t.name: t for t in create_profile_tools()}
        return tools

    @pytest.mark.asyncio
    async def test_get_profile_reads_about_you(self, profile_tools, deps, store):
        await store.write_about_you(deps.user_id, "## 关于你\n\n活泼")

        get_tool = profile_tools["get_profile"]
        result = await get_tool.execute({}, deps)
        assert "活泼" in result

    @pytest.mark.asyncio
    async def test_get_profile_fallback_to_memory(self, profile_tools, deps, store):
        await store.reset_user_memory(deps.user_id)
        await store.write_memory(deps.user_id, "## Long-term\n\n- entry item")

        get_tool = profile_tools["get_profile"]
        result = await get_tool.execute({}, deps)
        assert "entry item" in result

    @pytest.mark.asyncio
    async def test_update_profile_writes_memory(self, profile_tools, deps, store):
        update_tool = profile_tools["update_profile"]
        result = await update_tool.execute({"nickname": "小明", "bio": "程序员"}, deps)
        assert "已更新" in result

        content = await store.read_memory(deps.user_id)
        assert "nickname: 小明" in content
        assert "bio: 程序员" in content
