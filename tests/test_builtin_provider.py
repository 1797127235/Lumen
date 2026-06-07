"""测试 BuiltinMemoryProvider — L0 冻结快照、关键词 prefetch。"""

from __future__ import annotations

import pytest

from lib.memory.builtin_provider import BuiltinMemoryProvider
from lib.memory.markdown import AsyncMarkdownStore


class TestBuiltinMemoryProvider:
    @pytest.fixture
    async def store(self):
        s = AsyncMarkdownStore()
        yield s

    @pytest.fixture
    async def provider(self):
        return BuiltinMemoryProvider()

    @pytest.fixture
    def user_id(self):
        return "test_builtin_provider_unique"

    # ── 基础属性 ──

    def test_name(self, provider):
        assert provider.name == "builtin"

    @pytest.mark.asyncio
    async def test_is_always_available(self, provider):
        assert await provider.is_available() is True

    @pytest.mark.asyncio
    async def test_initialize_is_noop(self, provider):
        await provider.initialize("session_123")  # 不应报错

    @pytest.mark.asyncio
    async def test_get_tool_schemas_empty(self, provider):
        schemas = await provider.get_tool_schemas()
        assert schemas == []

    @pytest.mark.asyncio
    async def test_sync_turn_is_noop(self, provider):
        await provider.sync_turn("user msg", "ai msg", session_id="s1")  # 不应报错

    @pytest.mark.asyncio
    async def test_shutdown_is_noop(self, provider):
        await provider.shutdown()  # 不应报错

    # ── system_prompt_block (L0 冻结快照) ──

    @pytest.mark.asyncio
    async def test_system_prompt_returns_snapshot(self, provider, store, user_id):
        """PASS: 有记忆内容时返回非空快照。"""
        await store.write_memory(user_id, "## Long-term\n\n- 用户喜欢咖啡")
        result = await provider.system_prompt_block(user_id=user_id)
        assert "咖啡" in result

    @pytest.mark.asyncio
    async def test_system_prompt_empty_user_id(self, provider):
        """无 user_id 时返回空字符串。"""
        result = await provider.system_prompt_block(user_id="")
        assert result == ""

    @pytest.mark.asyncio
    async def test_system_prompt_no_content(self, provider, user_id):
        """无记忆内容时返回空字符串。"""
        result = await provider.system_prompt_block(user_id="nonexistent_builtin_user_xyz")
        assert result == ""

    @pytest.mark.asyncio
    async def test_system_prompt_includes_about_you(self, provider, store, user_id):
        """快照同时包含 MEMORY.md 和 USER.md。"""
        await store.write_memory(user_id, "记忆内容")
        await store.write_about_you(user_id, "画像内容")
        result = await provider.system_prompt_block(user_id=user_id)
        assert "记忆内容" in result
        assert "画像内容" in result

    # ── prefetch (关键词匹配) ──

    @pytest.mark.asyncio
    async def test_prefetch_finds_matching_paragraphs(self, provider, store, user_id):
        """PASS: 搜索关键词匹配 MEMORY.md 中的段落。"""
        await store.write_memory(
            user_id,
            "## Long-term notes\n\n- 用户在写 FastAPI 项目\n\n- 用户喜欢蓝色",
        )
        result = await provider.prefetch("FastAPI", user_id=user_id)
        assert "FastAPI" in result

    @pytest.mark.asyncio
    async def test_prefetch_no_match_returns_empty(self, provider, store, user_id):
        await store.write_memory(user_id, "## Long-term\n\n- 用户喜欢蓝色")
        result = await provider.prefetch("Python", user_id=user_id)
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_empty_query_returns_empty(self, provider, user_id):
        result = await provider.prefetch("", user_id=user_id)
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_no_user_id_returns_empty(self, provider):
        result = await provider.prefetch("test", user_id="")
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_empty_memory_returns_empty(self, provider, user_id):
        result = await provider.prefetch("test", user_id="nonexistent_prefetch_user_xyz")
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_multi_word_query(self, provider, store, user_id):
        """多词查询匹配任意一个关键词。"""
        await store.write_memory(
            user_id,
            "## Notes\n\n- 用户喜欢用 Python 写后端",
        )
        result = await provider.prefetch("Python 后端", user_id=user_id)
        assert "Python" in result

    @pytest.mark.asyncio
    async def test_prefetch_short_keywords_ignored(self, provider, store, user_id):
        """单字符关键词被忽略（len > 1）。"""
        await store.write_memory(user_id, "## Notes\n\n- a b c")
        result = await provider.prefetch("a b", user_id=user_id)
        # 'a' 和 'b' 都只有 1 个字符，被过滤后无有效关键词
        assert result == ""

    @pytest.mark.asyncio
    async def test_prefetch_multiple_matching_paragraphs(self, provider, store, user_id):
        """多个段落匹配时都返回。"""
        await store.write_memory(
            user_id,
            "## Notes\n\n- Python 是好语言\n\n- Java 也不错\n\n- 用户喜欢 Python 和 Rust",
        )
        result = await provider.prefetch("Python", user_id=user_id)
        assert "Python 是好语言" in result
        assert "Python 和 Rust" in result
