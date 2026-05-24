import asyncio

import pytest

from lib.bus.event_bus import EventBus
from lib.bus.queue import InboundMessage, MessageBus
from lib.chat.agent_runner import AgentRunner


@pytest.mark.asyncio
async def test_agent_runner_start_stop():
    bus = MessageBus()
    event_bus = EventBus()
    runner = AgentRunner(bus, event_bus)

    runner.start()
    await asyncio.sleep(0.01)
    await runner.stop()
    assert not runner._running


@pytest.mark.asyncio
async def test_agent_runner_process_message():
    bus = MessageBus()
    event_bus = EventBus()
    runner = AgentRunner(bus, event_bus)

    runner.start()

    # 发送消息
    from lib.bus.queue import InboundMessage

    await bus.publish_inbound(
        InboundMessage(
            channel="test",
            sender="user1",
            chat_id="chat1",
            content="Hello",
        )
    )

    # 等待处理
    await asyncio.sleep(0.1)

    # 接收回复
    await bus.dispatch_outbound_once()

    await runner.stop()


@pytest.mark.asyncio
async def test_agent_runner_error_handling():
    bus = MessageBus()
    event_bus = EventBus()
    runner = AgentRunner(bus, event_bus)

    # 模拟错误：直接测试错误回复
    await runner._process_message(
        InboundMessage(
            channel="test",
            sender="user1",
            chat_id="chat1",
            content="test",
        )
    )

    # 应该能正常处理，不抛异常
    # 占位实现会返回 "Echo: test"
    await bus.dispatch_outbound_once()
