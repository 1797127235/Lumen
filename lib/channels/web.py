from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from lib.bus.event_bus import EventBus, StreamDeltaReady
from lib.bus.queue import InboundMessage, MessageBus, OutboundMessage
from lib.channels.base import BaseChannel

logger = logging.getLogger(__name__)


class WebChannel(BaseChannel):
    """Web SSE Channel"""

    def __init__(self, bus: MessageBus, event_bus: EventBus) -> None:
        self._bus = bus
        self._event_bus = event_bus
        # session_key -> queue
        self._streams: dict[str, asyncio.Queue[str | None]] = {}

    async def start(self) -> None:
        """启动：订阅出站消息和流式事件"""
        self._bus.subscribe_outbound("web", self._on_response)
        self._event_bus.on(StreamDeltaReady, self._on_stream_delta)
        logger.info("WebChannel started")

    async def stop(self) -> None:
        """清理所有 stream"""
        for queue in self._streams.values():
            await queue.put(None)
        self._streams.clear()

    async def send_message(self, chat_id: str, content: str, **kwargs) -> None:
        """WebChannel 不走 send_message，走 SSE stream"""
        pass

    async def handle_request(
        self,
        user_id: str,
        conversation_id: str | None,
        message: str,
    ) -> AsyncIterator[str]:
        """处理 HTTP 请求，产出 SSE 事件流"""
        # 如果没有 conversation_id，创建一个新的
        if not conversation_id:
            conversation_id = str(uuid.uuid4())

        session_key = f"web:{conversation_id}"
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._streams[session_key] = queue

        # 发送入站消息到 Bus
        await self._bus.publish_inbound(
            InboundMessage(
                channel="web",
                sender=user_id,
                chat_id=conversation_id,
                content=message,
            )
        )

        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=300.0)
                if event is None:  # 结束标记
                    break
                yield event
        except TimeoutError:
            logger.warning(f"Web request timeout: {session_key}")
            data = json.dumps(
                {
                    "type": "error",
                    "message": "请求超时",
                },
                ensure_ascii=False,
            )
            yield f"data: {data}\n\n"
        finally:
            self._streams.pop(session_key, None)

    async def _on_response(self, msg: OutboundMessage) -> None:
        """接收最终回复"""
        session_key = f"web:{msg.chat_id}"
        queue = self._streams.get(session_key)
        if queue:
            data = json.dumps(
                {
                    "type": "done",
                    "content": msg.content,
                    "conversation_id": msg.chat_id,
                },
                ensure_ascii=False,
            )
            await queue.put(f"data: {data}\n\n")
            await queue.put(None)  # 结束标记

    async def _on_stream_delta(self, event: StreamDeltaReady) -> None:
        """接收流式输出片段"""
        queue = self._streams.get(event.session_key)
        if queue and event.content_delta:
            data = json.dumps(
                {
                    "type": "token",
                    "content": event.content_delta,
                },
                ensure_ascii=False,
            )
            await queue.put(f"data: {data}\n\n")
