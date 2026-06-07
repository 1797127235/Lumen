from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from channels.telegram import TelegramChannel
from lib.bus.event_bus import EventBus
from lib.bus.queue import MessageBus


@pytest.mark.asyncio
async def test_telegram_channel_message_handling():
    bus = MessageBus()
    event_bus = EventBus()

    with patch("channels.telegram.channel.Application") as MockApp:
        mock_app = MagicMock()
        MockApp.builder.return_value.token.return_value.request.return_value.build.return_value = mock_app

        channel = TelegramChannel("fake_token", bus, event_bus)

        # 模拟收到文本消息
        mock_update = MagicMock()
        mock_update.effective_chat.id = 123456
        mock_update.effective_user.id = 789
        mock_update.effective_message.text = "Hello"

        await channel._on_text(mock_update, None)

        # 验证消息已进入 Bus
        msg = await bus.consume_inbound()
        assert msg is not None
        assert msg.content == "Hello"
        assert msg.channel == "telegram"
        assert msg.media == []


@pytest.mark.asyncio
async def test_telegram_channel_on_response():
    bus = MessageBus()
    event_bus = EventBus()

    with patch("channels.telegram.channel.Application") as MockApp:
        mock_app = MagicMock()
        mock_app.bot = AsyncMock()
        MockApp.builder.return_value.token.return_value.request.return_value.build.return_value = mock_app

        channel = TelegramChannel("fake_token", bus, event_bus)

        # 测试发送回复
        from lib.bus.queue import OutboundMessage

        await channel._on_response(
            OutboundMessage(
                channel="telegram",
                chat_id="123456",
                content="Reply",
            )
        )

        # 验证 bot.send_message 被调用
        mock_app.bot.send_message.assert_called_once()
