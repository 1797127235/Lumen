"""Web Channel 内置插件。"""

from __future__ import annotations

from typing import Any

from channels.builtins.web.web import WebChannel
from channels.provider import ChannelProvider
from lib.bus.event_bus import EventBus
from lib.bus.queue import MessageBus


class WebChannelProvider(ChannelProvider):
    """Web SSE Channel Provider。"""

    @property
    def name(self) -> str:
        return "web"

    def get_config_schema(self) -> list[dict]:
        return []

    def build(
        self,
        config: dict[str, Any],
        *,
        bus: MessageBus,
        event_bus: EventBus,
    ) -> WebChannel:
        instance_name = config.get("instance_name", "")
        return WebChannel(bus, event_bus, instance_name=instance_name)
