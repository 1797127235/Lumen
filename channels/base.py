from __future__ import annotations

from abc import ABC, abstractmethod

from lib.bus.queue import OutboundMessage


class BaseChannel(ABC):
    """平台 Channel 抽象基类"""

    def __init__(self, instance_name: str = "") -> None:
        self._instance_name = instance_name

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel 类型名，如 web / telegram / discord。"""
        ...

    @property
    def instance_name(self) -> str:
        """Channel 实例名（配置实例名），为空时 fallback 到 name。"""
        return self._instance_name or self.name

    @abstractmethod
    async def start(self) -> None:
        """启动 Channel"""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """停止 Channel"""
        ...

    @abstractmethod
    async def send_message(self, chat_id: str, content: str, **kwargs) -> None:
        """发送消息到平台"""
        ...

    def capabilities(self) -> set[str]:
        """返回该 channel 支持的能力集合。

        示例：{"text", "media", "group", "streaming"}
        """
        return set()

    async def _on_response(self, msg: OutboundMessage) -> None:
        """处理出站消息（子类可覆盖以自定义行为）"""
        await self.send_message(msg.chat_id, msg.content)
