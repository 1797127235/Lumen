import asyncio

import pytest

from lib.bus.queue import InboundMessage, MessageBus, OutboundMessage


@pytest.mark.asyncio
async def test_publish_and_consume_inbound():
    bus = MessageBus()
    msg = InboundMessage(
        channel="test",
        sender="user1",
        chat_id="chat1",
        content="hello",
    )
    await bus.publish_inbound(msg)
    result = await bus.consume_inbound()
    assert result is not None
    assert result.content == "hello"
    assert result.session_key == "test:chat1"


@pytest.mark.asyncio
async def test_publish_outbound_and_dispatch():
    bus = MessageBus()
    received = []

    async def callback(msg: OutboundMessage):
        received.append(msg)

    bus.subscribe_outbound("test", callback)
    await bus.publish_outbound(
        OutboundMessage(
            channel="test",
            chat_id="chat1",
            content="reply",
        )
    )

    # dispatch_outbound_once 是单次分发（用于测试）
    await bus.dispatch_outbound_once()
    assert len(received) == 1
    assert received[0].content == "reply"


@pytest.mark.asyncio
async def test_no_subscriber_warning():
    bus = MessageBus()
    await bus.publish_outbound(
        OutboundMessage(
            channel="no_sub",
            chat_id="chat1",
            content="test",
        )
    )
    # 应该不报错，只是记录警告
    await bus.dispatch_outbound_once()


@pytest.mark.asyncio
async def test_stop_wakes_consumer():
    bus = MessageBus()

    async def consumer():
        msg = await bus.consume_inbound()
        return msg

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.01)  # 让 consumer 开始等待
    bus.stop()

    result = await asyncio.wait_for(task, timeout=1.0)
    assert result is None
