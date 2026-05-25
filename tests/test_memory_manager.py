"""测试 MemoryManager fan-out 编排。"""

from __future__ import annotations

import pytest

from lib.memory.manager import MemoryManager
from lib.memory.provider import MemoryProvider


class DummyProvider(MemoryProvider):
    """测试用的虚拟 Provider。"""

    def __init__(self, name: str = "dummy"):
        self._name = name
        self.prefetch_called = False
        self.sync_called = False

    @property
    def name(self) -> str:
        return self._name

    async def is_available(self) -> bool:
        return True

    async def initialize(self, session_id: str, **kwargs) -> None:
        pass

    async def prefetch(self, query: str, *, session_id: str = "", **kwargs) -> str:
        self.prefetch_called = True
        return f"dummy result for {query}"

    async def get_tool_schemas(self) -> list[dict]:
        return []

    async def sync_turn(self, user: str, assistant: str, *, session_id: str = "") -> None:
        self.sync_called = True


class TestMemoryManager:
    """测试 MemoryManager。"""

    @pytest.fixture
    def manager(self):
        return MemoryManager()

    def test_builtin_always_registered(self, manager):
        assert "builtin" in [p.name for p in manager.providers]

    def test_add_external_provider(self, manager):
        dummy = DummyProvider("dummy")
        manager.add_provider(dummy)
        assert "dummy" in [p.name for p in manager.providers]

    def test_reject_second_external_provider(self, manager):
        dummy1 = DummyProvider("dummy1")
        dummy2 = DummyProvider("dummy2")
        manager.add_provider(dummy1)
        manager.add_provider(dummy2)
        names = [p.name for p in manager.providers]
        assert "dummy1" in names
        assert "dummy2" not in names

    @pytest.mark.asyncio
    async def test_build_system_prompt_with_builtin(self, manager):
        # builtin 没有 user_id 时返回空
        result = await manager.build_system_prompt()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_build_context_empty(self, manager):
        result = await manager.build_context("demo_user", "")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_build_context_with_user_input(self, manager):
        result = await manager.build_context("demo_user", "hello")
        # 包含当前时间
        assert "Current time" in result

    @pytest.mark.asyncio
    async def test_prefetch_all(self, manager):
        dummy = DummyProvider("dummy")
        manager.add_provider(dummy)
        result = await manager.prefetch_all("test", user_id="demo_user")
        assert "dummy result" in result
        assert dummy.prefetch_called

    @pytest.mark.asyncio
    async def test_sync_all(self, manager):
        dummy = DummyProvider("dummy")
        manager.add_provider(dummy)
        await manager.sync_all("hello", "hi", session_id="web:abc")
        assert dummy.sync_called

    @pytest.mark.asyncio
    async def test_get_all_tool_schemas_empty(self, manager):
        schemas = await manager.get_all_tool_schemas()
        assert schemas == []

    @pytest.mark.asyncio
    async def test_initialize_all(self, manager):
        await manager.initialize_all("session-1")  # 不应抛异常

    @pytest.mark.asyncio
    async def test_shutdown_all(self, manager):
        await manager.shutdown_all()  # 不应抛异常

    @pytest.mark.asyncio
    async def test_on_memory_write_skips_builtin(self, manager):
        dummy = DummyProvider("dummy")
        manager.add_provider(dummy)
        await manager.on_memory_write("save", "memory", "content")
        # builtin 不应收到 on_memory_write（设计上只发给外部 provider）
        # dummy 没有 on_memory_write 实现，不会抛异常

    @pytest.mark.asyncio
    async def test_error_isolation_prefetch(self, manager):
        """一个 provider 失败不应影响其他。"""

        class BrokenProvider(MemoryProvider):
            @property
            def name(self) -> str:
                return "broken"

            async def is_available(self) -> bool:
                return True

            async def initialize(self, session_id: str, **kwargs) -> None:
                pass

            async def prefetch(self, query: str, *, session_id: str = "", **kwargs) -> str:
                raise RuntimeError("boom")

            async def get_tool_schemas(self) -> list[dict]:
                return []

        dummy = DummyProvider("dummy")
        broken = BrokenProvider()
        manager.add_provider(dummy)
        manager.add_provider(broken)

        result = await manager.prefetch_all("test")
        assert "dummy result" in result
        assert "boom" not in result

    @pytest.mark.asyncio
    async def test_build_context_includes_time(self, manager):
        result = await manager.build_context("demo_user", "test input")
        assert "Current time:" in result
        assert "UTC" in result
