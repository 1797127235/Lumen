"""测试 MemoryProvider 接口与 NoOpMemoryProvider。"""

from __future__ import annotations

import pytest

from lib.memory.provider import NoOpMemoryProvider


class TestMemoryProvider:
    """测试抽象基类签名。"""

    def test_noop_name(self):
        p = NoOpMemoryProvider()
        assert p.name == "noop"

    @pytest.mark.asyncio
    async def test_noop_is_available(self):
        p = NoOpMemoryProvider()
        assert await p.is_available() is True

    @pytest.mark.asyncio
    async def test_noop_initialize(self):
        p = NoOpMemoryProvider()
        await p.initialize("session-1")  # 不应抛异常

    @pytest.mark.asyncio
    async def test_noop_prefetch_empty(self):
        p = NoOpMemoryProvider()
        result = await p.prefetch("test query")
        assert result == ""

    @pytest.mark.asyncio
    async def test_noop_get_tool_schemas_empty(self):
        p = NoOpMemoryProvider()
        schemas = await p.get_tool_schemas()
        assert schemas == []

    @pytest.mark.asyncio
    async def test_noop_handle_tool_call_empty(self):
        p = NoOpMemoryProvider()
        result = await p.handle_tool_call("foo", {})
        assert result == ""

    @pytest.mark.asyncio
    async def test_noop_shutdown(self):
        p = NoOpMemoryProvider()
        await p.shutdown()  # 不应抛异常

    @pytest.mark.asyncio
    async def test_noop_optional_hooks_noop(self):
        p = NoOpMemoryProvider()
        await p.on_turn_start(1, "hello")
        await p.on_session_end([])
        await p.on_session_switch("new-id")
        await p.on_pre_compress([])
        await p.on_memory_write("save", "memory", "content")
        await p.on_delegation("task", "result")
        assert await p.get_config_schema() == []
        await p.save_config({}, "~/.lumen")
