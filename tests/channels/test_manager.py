"""测试 ChannelManager 的发现、启动、停止。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from channels.base import BaseChannel
from channels.manager import ChannelManager
from channels.models import ChannelConfig
from channels.provider import ChannelProvider
from lib.bus.event_bus import EventBus
from lib.bus.queue import MessageBus


class FakeChannel(BaseChannel):
    def __init__(self, instance_name: str = ""):
        super().__init__(instance_name=instance_name)
        self.started = False
        self.stopped = False

    @property
    def name(self) -> str:
        return "fake"

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_message(self, chat_id: str, content: str, **kwargs) -> None:
        pass


class FakeProvider(ChannelProvider):
    @property
    def name(self) -> str:
        return "fake"

    def build(
        self,
        config: dict[str, Any],
        *,
        bus: MessageBus,
        event_bus: EventBus,
    ) -> BaseChannel:
        return FakeChannel(instance_name=config.get("instance_name", ""))


@pytest.fixture
def manager():
    return ChannelManager(bus=MessageBus(), event_bus=EventBus())


@pytest.mark.asyncio
async def test_start_channels_skips_disabled(manager: ChannelManager):
    configs = [
        ChannelConfig(name="fake-1", provider_type="fake", enabled=False),
    ]
    # 没有 discovered fake provider，所以实际上也不会启动；这里主要测 enabled 过滤不报错
    channels = await manager.start_channels(configs)
    assert channels == []


@pytest.mark.asyncio
async def test_start_channels_builds_and_starts(manager: ChannelManager):
    provider = FakeProvider()
    manager._providers = {"fake": type(provider)}

    configs = [
        ChannelConfig(name="fake-1", provider_type="fake", enabled=True, config={}),
    ]
    channels = await manager.start_channels(configs)

    assert len(channels) == 1
    assert isinstance(channels[0], FakeChannel)
    assert channels[0].started
    assert channels[0].instance_name == "fake-1"


@pytest.mark.asyncio
async def test_stop_channels_stops_all(manager: ChannelManager):
    ch1 = FakeChannel()
    ch2 = FakeChannel()
    await manager.stop_channels([ch1, ch2])

    assert ch1.stopped
    assert ch2.stopped


@pytest.mark.asyncio
async def test_start_channels_continues_after_failure(manager: ChannelManager):
    class FailingProvider(ChannelProvider):
        @property
        def name(self) -> str:
            return "failing"

        def build(self, config, *, bus, event_bus):
            raise RuntimeError("boom")

    manager._providers = {
        "failing": FailingProvider,
        "fake": FakeProvider,
    }

    configs = [
        ChannelConfig(name="failing", provider_type="failing", enabled=True),
        ChannelConfig(name="fake", provider_type="fake", enabled=True),
    ]
    channels = await manager.start_channels(configs)

    assert len(channels) == 1
    assert channels[0].name == "fake"


def test_discover_providers_from_temp_dirs(manager: ChannelManager):
    with tempfile.TemporaryDirectory() as builtin_tmp, tempfile.TemporaryDirectory() as user_tmp:
        builtin_path = Path(builtin_tmp)
        user_path = Path(user_tmp)

        # builtin fake provider
        plugin_dir = builtin_path / "fake"
        plugin_dir.mkdir()
        (plugin_dir / "__init__.py").write_text(
            "from channels.provider import ChannelProvider\n"
            "from channels.base import BaseChannel\n"
            "class Provider(ChannelProvider):\n"
            "    @property\n"
            "    def name(self): return 'fake'\n"
            "    def build(self, config, *, bus, event_bus): return BaseChannel.__new__(BaseChannel)\n",
            encoding="utf-8",
        )
        (plugin_dir / "plugin.yaml").write_text(
            "name: fake\nversion: '0.1.0'\n",
            encoding="utf-8",
        )

        discovered = manager.discover_providers(builtin_dir=builtin_path, user_dir=user_path)
        assert "fake" in discovered
