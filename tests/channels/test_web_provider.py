"""测试 WebChannel 内置 Provider。"""

from __future__ import annotations

import pytest

from channels.web import WebChannelProvider
from channels.web.web import WebChannel
from lib.bus.event_bus import EventBus
from lib.bus.queue import MessageBus


@pytest.mark.asyncio
async def test_web_provider_builds_channel():
    provider = WebChannelProvider()
    assert provider.name == "web"

    bus = MessageBus()
    event_bus = EventBus()
    channel = provider.build({}, bus=bus, event_bus=event_bus)

    assert isinstance(channel, WebChannel)
    assert channel.name == "web"
    assert channel.capabilities() == {"text", "streaming"}
