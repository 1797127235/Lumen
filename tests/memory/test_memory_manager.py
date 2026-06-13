"""测试 MemoryManager 的 provider 注册与 fan-out。"""

from __future__ import annotations

import pytest

from lib.memory.manager import MemoryManager
from lib.memory.provider import MemoryProvider


class FakeProvider(MemoryProvider):
    def __init__(self, name: str):
        self._name = name
        self.writes: list[tuple[str, str, str, dict | None]] = []
        self.available = True

    @property
    def name(self) -> str:
        return self._name

    async def is_available(self) -> bool:
        return self.available

    async def initialize(self, session_id: str, **kwargs) -> None:
        pass

    async def get_tool_schemas(self) -> list[dict]:
        return []

    async def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        self.writes.append((action, target, content, metadata))


@pytest.fixture
def manager():
    return MemoryManager()


def test_builtin_provider_always_present(manager):
    names = [p.name for p in manager.providers]
    assert "builtin" in names


def test_multiple_external_providers_allowed(manager):
    p1 = FakeProvider("p1")
    p2 = FakeProvider("p2")

    manager.add_provider(p1)
    manager.add_provider(p2)

    names = [p.name for p in manager.providers]
    assert "p1" in names
    assert "p2" in names


def test_provider_override(manager):
    p1 = FakeProvider("same")
    p2 = FakeProvider("same")

    manager.add_provider(p1)
    manager.add_provider(p2)

    providers = [p for p in manager.providers if p.name == "same"]
    assert len(providers) == 1
    assert providers[0] is p2


def test_multiple_instances_of_same_type(manager):
    """同名 provider type 用不同 instance_name 可以共存。"""
    p1 = FakeProvider("honcho")
    p2 = FakeProvider("honcho")

    manager.add_provider(p1, instance_name="honcho-prod")
    manager.add_provider(p2, instance_name="honcho-dev")

    names = [p.display_name for p in manager.providers if p.name == "honcho"]
    assert sorted(names) == ["honcho-dev", "honcho-prod"]

    assert manager.get_provider("honcho-prod") is p1
    assert manager.get_provider("honcho-dev") is p2


def test_on_memory_write_fanout(manager):
    p1 = FakeProvider("p1")
    p2 = FakeProvider("p2")

    manager.add_provider(p1)
    manager.add_provider(p2)

    # builtin 不应收到自己的写入事件；on_memory_write 内部已跳过 builtin
    # 因此只有 p1, p2 收到


async def test_on_memory_write_async(manager):
    p1 = FakeProvider("p1")
    p2 = FakeProvider("p2")

    manager.add_provider(p1)
    manager.add_provider(p2)

    await manager.on_memory_write("save", "memory", "test content", metadata={"user_id": "u1"})

    assert len(p1.writes) == 1
    assert p1.writes[0] == ("save", "memory", "test content", {"user_id": "u1"})
    assert len(p2.writes) == 1
    assert p2.writes[0] == ("save", "memory", "test content", {"user_id": "u1"})


def test_remove_provider(manager):
    p1 = FakeProvider("p1")
    manager.add_provider(p1)

    assert manager.remove_provider("p1") is True
    names = [p.name for p in manager.providers]
    assert "p1" not in names


def test_remove_builtin_forbidden(manager):
    assert manager.remove_provider("builtin") is False


def test_clear_external_providers(manager):
    p1 = FakeProvider("p1")
    p2 = FakeProvider("p2")
    manager.add_provider(p1)
    manager.add_provider(p2)

    manager.clear_external_providers()

    names = [p.name for p in manager.providers]
    assert names == ["builtin"]
