"""Telegram Channel 内置插件。"""

from __future__ import annotations

from typing import Any

from channels.builtins.telegram.channel import TelegramChannel
from channels.provider import ChannelProvider
from lib.bus.event_bus import EventBus
from lib.bus.queue import MessageBus


class TelegramChannelProvider(ChannelProvider):
    """Telegram Polling Channel Provider。"""

    @property
    def name(self) -> str:
        return "telegram"

    def get_config_schema(self) -> list[dict]:
        return [
            {
                "name": "bot_token",
                "type": "string",
                "label": "Bot Token",
                "description": "Telegram Bot Token（从 @BotFather 获取）",
                "sensitive": True,
            },
        ]

    def build(
        self,
        config: dict[str, Any],
        *,
        bus: MessageBus,
        event_bus: EventBus,
    ) -> TelegramChannel:
        token = config.get("bot_token", "")
        if not token:
            raise ValueError("Telegram channel 缺少 bot_token 配置")
        instance_name = config.get("instance_name", "")
        return TelegramChannel(token, bus, event_bus, instance_name=instance_name)
