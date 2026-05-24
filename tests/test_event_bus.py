import asyncio

import pytest

from lib.bus.event_bus import EventBus, StreamDeltaReady, TurnStarted


@pytest.mark.asyncio
async def test_emit_and_receive_event():
    bus = EventBus()
    received = []

    async def handler(event: TurnStarted):
        received.append(event)

    bus.on(TurnStarted, handler)
    bus.emit(TurnStarted(channel="test", session_key="test:1", chat_id="1", content="hi"))

    # 给事件处理一点时间
    await asyncio.sleep(0.01)
    assert len(received) == 1
    assert received[0].content == "hi"


@pytest.mark.asyncio
async def test_multiple_handlers():
    bus = EventBus()
    received1 = []
    received2 = []

    async def handler1(event: TurnStarted):
        received1.append(event)

    async def handler2(event: TurnStarted):
        received2.append(event)

    bus.on(TurnStarted, handler1)
    bus.on(TurnStarted, handler2)

    bus.emit(TurnStarted(channel="test", session_key="test:1", chat_id="1", content="hi"))

    await asyncio.sleep(0.01)
    assert len(received1) == 1
    assert len(received2) == 1


@pytest.mark.asyncio
async def test_different_event_types():
    bus = EventBus()
    turn_received = []
    stream_received = []

    async def turn_handler(event: TurnStarted):
        turn_received.append(event)

    async def stream_handler(event: StreamDeltaReady):
        stream_received.append(event)

    bus.on(TurnStarted, turn_handler)
    bus.on(StreamDeltaReady, stream_handler)

    bus.emit(TurnStarted(channel="test", session_key="test:1", chat_id="1", content="hi"))
    bus.emit(StreamDeltaReady(channel="test", session_key="test:1", chat_id="1", content_delta="hello"))

    await asyncio.sleep(0.01)
    assert len(turn_received) == 1
    assert len(stream_received) == 1
    assert stream_received[0].content_delta == "hello"


@pytest.mark.asyncio
async def test_no_handler():
    bus = EventBus()
    # 没有订阅者时，emit 应该不报错
    bus.emit(TurnStarted(channel="test", session_key="test:1", chat_id="1", content="hi"))
    await asyncio.sleep(0.01)
    # 没有断言，只要不抛异常就算通过
