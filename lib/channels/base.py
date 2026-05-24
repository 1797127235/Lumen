from __future__ import annotations

from abc import ABC, abstractmethod

from lib.bus.queue import OutboundMessage


class BaseChannel(ABC):
    """平台 Channel 抽象基类"""

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

    async def _on_response(self, msg: OutboundMessage) -> None:
        """处理出站消息（子类可覆盖以自定义行为）"""
        await self.send_message(msg.chat_id, msg.content)
