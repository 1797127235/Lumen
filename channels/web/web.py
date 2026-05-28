from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator

from channels.base import BaseChannel
from channels.web.formatters import SSEFormatter
from lib.bus.event_bus import EventBus, StreamDeltaReady, TraceReady
from lib.bus.queue import InboundMessage, MessageBus, OutboundMessage

logger = logging.getLogger(__name__)


class WebChannel(BaseChannel):
    """Web SSE Channel"""

    def __init__(self, bus: MessageBus, event_bus: EventBus) -> None:
        self._bus = bus
        self._event_bus = event_bus
        self._formatter = SSEFormatter()
        # session_key -> queue
        self._streams: dict[str, asyncio.Queue[str | None]] = {}

    async def start(self) -> None:
        """启动：订阅出站消息和流式事件"""
        self._bus.subscribe_outbound("web", self._on_response)
        self._event_bus.on(StreamDeltaReady, self._on_stream_delta)
        self._event_bus.on(TraceReady, self._on_trace)
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
        model: str | None = None,
        provider: str | None = None,
    ) -> AsyncIterator[str]:
        """处理 HTTP 请求，产出 SSE 事件流"""
        if not conversation_id:
            conversation_id = str(uuid.uuid4())

        session_key = f"web:{conversation_id}"
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._streams[session_key] = queue

        await self._bus.publish_inbound(
            InboundMessage(
                channel="web",
                sender=user_id,
                chat_id=conversation_id,
                content=message,
                model=model,
                provider=provider,
            )
        )

        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=300.0)
                if event is None:
                    break
                yield event
        except TimeoutError:
            logger.warning(f"Web request timeout: {session_key}")
            yield self._formatter.format_error("请求超时")
        finally:
            self._streams.pop(session_key, None)

    async def _on_response(self, msg: OutboundMessage) -> None:
        """接收最终回复"""
        session_key = f"web:{msg.chat_id}"
        queue = self._streams.get(session_key)
        if queue:
            await queue.put(self._formatter.format_done(msg.content, msg.chat_id))
            await queue.put(None)

    async def _on_stream_delta(self, event: StreamDeltaReady) -> None:
        """接收流式输出片段"""
        queue = self._streams.get(event.session_key)
        if not queue:
            return
        if event.thinking_delta:
            await queue.put(self._formatter.format_thinking(event.thinking_delta))
        if event.content_delta:
            await queue.put(self._formatter.format_token(event.content_delta))

    async def _on_trace(self, event: TraceReady) -> None:
        """接收工具调用 trace 事件"""
        queue = self._streams.get(event.session_key)
        if queue:
            await queue.put(self._formatter.format_trace(event.kind, event.tool, event.content))
