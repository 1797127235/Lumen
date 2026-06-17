from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RawFile:
    """Channel 提取的原始文件引用 — 平台无关。

    Channel 负责从平台 API 下载文件到本地暂存目录，
    然后用此结构将文件路径和元数据传递给 AttachmentService。
    """

    path: str
    original_name: str = ""
    mime_type: str | None = None
    size: int = 0


@dataclass
class InboundMessage:
    """从 Channel 传入的消息"""

    channel: str
    sender: str
    chat_id: str
    content: str
    media: list[RawFile] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        return f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Agent 发出的消息"""

    channel: str
    chat_id: str
    content: str
    thinking: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class MessageBus:
    """异步消息总线"""

    def __init__(self) -> None:
        self._inbound: asyncio.Queue[InboundMessage | None] = asyncio.Queue()
        self._outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._subscribers: dict[str, list[Callable[[OutboundMessage], Awaitable[None]]]] = {}
        self._running = False

    async def publish_inbound(self, msg: InboundMessage) -> None:
        await self._inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage | None:
        return await self._inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        await self._outbound.put(msg)

    def subscribe_outbound(
        self,
        channel: str,
        callback: Callable[[OutboundMessage], Awaitable[None]],
    ) -> None:
        self._subscribers.setdefault(channel, []).append(callback)

    async def dispatch_outbound(self) -> None:
        """后台任务：持续分发出站消息"""
        self._running = True
        while self._running:
            try:
                msg = await asyncio.wait_for(self._outbound.get(), timeout=1.0)
                await self._dispatch_single(msg)
            except TimeoutError:
                continue

    async def dispatch_outbound_once(self) -> None:
        """单次分发（用于测试）"""
        try:
            msg = await asyncio.wait_for(self._outbound.get(), timeout=1.0)
            await self._dispatch_single(msg)
        except TimeoutError:
            pass

    async def _dispatch_single(self, msg: OutboundMessage) -> None:
        callbacks = self._subscribers.get(msg.channel, [])
        if not callbacks:
            logger.warning(f"No subscriber for channel: {msg.channel}")
            return

        for cb in callbacks:
            try:
                await cb(msg)
            except Exception as e:
                logger.error(f"Dispatch to {msg.channel} failed: {e}")

    def stop(self) -> None:
        self._running = False
        # 放入 None 唤醒 consume_inbound
        with contextlib.suppress(asyncio.QueueFull):
            self._inbound.put_nowait(None)


# -- 全局单例 --
# 主动送达层(lib/proactive/delivery.py)等后台组件需要拿到 bus 发布 InboundMessage,
# 但它们不在 startup 的 lifespan 作用域内。startup 创建 MessageBus 时调 set_bus() 注册,
# 其他地方用 get_bus() 取。
_bus: MessageBus | None = None


def set_bus(bus: MessageBus) -> None:
    """注册全局 MessageBus 单例(startup 调用)。"""
    global _bus
    _bus = bus


def get_bus() -> MessageBus | None:
    """取全局 MessageBus 单例(未注册时返回 None,调用方自行兜底)。"""
    return _bus
