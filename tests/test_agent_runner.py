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


def test_turnstate_has_in_think_tag_for_text_streaming():
    """回归：_TurnState 必须有 in_think_tag。

    缺它时模型一开始流式输出最终答案（TextPart）就会
    AttributeError，表现为「思考完不回复」。
    """
    from types import SimpleNamespace

    from pydantic_ai.messages import TextPart

    from lib.chat.agent_runner import _TurnState
    from lib.chat.event_handlers import EventHandlers

    state = _TurnState()
    assert hasattr(state, "in_think_tag")

    event = SimpleNamespace(part=TextPart(content="你好"))
    items = EventHandlers.part_start(event, state, {"conversation_id": "c1"})

    assert any(it.get("type") == "token" for it in items)
    assert state.full_content == "你好"
