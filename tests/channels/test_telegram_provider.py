"""测试 TelegramChannel 内置 Provider。"""

from __future__ import annotations

import pytest

from channels.telegram import TelegramChannelProvider
from channels.telegram.channel import TelegramChannel
from lib.bus.event_bus import EventBus
from lib.bus.queue import MessageBus


def test_telegram_provider_name_and_schema():
    provider = TelegramChannelProvider()
    assert provider.name == "telegram"
    schema = provider.get_config_schema()
    assert any(s["name"] == "bot_token" for s in schema)


def test_telegram_provider_builds_channel():
    provider = TelegramChannelProvider()
    bus = MessageBus()
    event_bus = EventBus()
    channel = provider.build({"bot_token": "test-token"}, bus=bus, event_bus=event_bus)

    assert isinstance(channel, TelegramChannel)
    assert channel.name == "telegram"


def test_telegram_provider_missing_token_raises():
    provider = TelegramChannelProvider()
    with pytest.raises(ValueError, match="bot_token"):
        provider.build({}, bus=MessageBus(), event_bus=EventBus())
