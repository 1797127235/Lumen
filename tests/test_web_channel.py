import asyncio

import pytest

from lib.bus.event_bus import EventBus, StreamDeltaReady
from lib.bus.queue import MessageBus, OutboundMessage
from channels.web.web import WebChannel


@pytest.mark.asyncio
async def test_web_channel_on_response():
    """测试 _on_response 直接写入队列"""
    bus = MessageBus()
    event_bus = EventBus()
    channel = WebChannel(bus, event_bus)
    await channel.start()

    # 注册一个 stream
    session_key = "web:conv1"
    queue = asyncio.Queue()
    channel._streams[session_key] = queue

    # 发送最终回复
    await channel._on_response(
        OutboundMessage(
            channel="web",
            chat_id="conv1",
            content="Hello back",
        )
    )

    # 读取队列
    event1 = await asyncio.wait_for(queue.get(), timeout=1.0)
    event2 = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert event1 is not None
    assert '"type": "done"' in event1
    assert "Hello back" in event1
    assert event2 is None  # 结束标记


@pytest.mark.asyncio
async def test_web_channel_stream_delta():
    """测试流式事件写入队列"""
    bus = MessageBus()
    event_bus = EventBus()
    channel = WebChannel(bus, event_bus)
    await channel.start()

    # 注册一个 stream
    session_key = "web:conv2"
    queue = asyncio.Queue()
    channel._streams[session_key] = queue

    # 发送流式事件
    event_bus.emit(
        StreamDeltaReady(
            channel="web",
            session_key=session_key,
            chat_id="conv2",
            content_delta="Hello",
        )
    )

    await asyncio.sleep(0.01)

    # 读取队列
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event is not None
    assert '"type": "token"' in event
    assert "Hello" in event
