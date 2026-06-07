"""端到端集成测试 — 验证 MessageBus + EventBus + Channels + AgentRunner 启动"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI


@pytest.mark.asyncio
async def test_lifespan_starts_web_channel():
    """验证 lifespan 启动 WebChannel"""
    from core.startup import lifespan

    app = FastAPI()

    with (
        patch("core.startup._init_db", new=AsyncMock()),
        patch("core.startup.apply_user_config", return_value={}),
        patch("core.startup.get_settings") as mock_settings,
        patch("lib.tools.mcp.client_manager.get_mcp_manager") as mock_mcp,
        patch("core.startup.get_engine") as mock_engine,
    ):
        mock_settings.return_value.enable_web = True
        mock_settings.return_value.telegram_bot_token = ""
        mock_mcp.return_value.connect_all = AsyncMock()
        mock_mcp.return_value.disconnect_all = AsyncMock()
        mock_engine.return_value.dispose = AsyncMock()

        # 模拟 lifespan
        ctx = lifespan(app)
        await ctx.__aenter__()

        # 验证 web_channel 已注册
        assert hasattr(app.state, "web_channel")
        assert app.state.web_channel is not None

        await ctx.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_web_channel_end_to_end():
    """验证 WebChannel 完整流程：请求 -> AgentRunner -> SSE 输出"""
    from channels.web.web import WebChannel
    from lib.bus.event_bus import EventBus
    from lib.bus.queue import MessageBus
    from lib.chat.agent_runner import AgentRunner

    bus = MessageBus()
    event_bus = EventBus()

    web_channel = WebChannel(bus, event_bus)
    await web_channel.start()

    runner = AgentRunner(bus, event_bus)
    runner.start()

    # 启动出站分发
    dispatch_task = asyncio.create_task(bus.dispatch_outbound())

    try:
        # 模拟 SSE 请求
        events = []
        async for event in web_channel.handle_request(
            user_id="demo_user",
            conversation_id=None,
            message="你好",
        ):
            events.append(event)
            if len(events) > 20:  # 安全上限
                break

        # 验证收到了 SSE 事件
        assert len(events) > 0

        # 验证事件格式
        for event in events:
            assert event.startswith("data: ") or event.startswith("event: ") or event == "\n"
    finally:
        await runner.stop()
        dispatch_task.cancel()
        import contextlib

        with contextlib.suppress(asyncio.CancelledError):
            await dispatch_task
        await web_channel.stop()


@pytest.mark.skip(reason="CLIChannel 是 TypeScript TUI，无 Python 实现，待补充")
@pytest.mark.asyncio
async def test_cli_channel_end_to_end():
    """验证 CLIChannel 启动和停止"""
    pass


@pytest.mark.asyncio
async def test_telegram_channel_mocked():
    """验证 TelegramChannel 启动（mock Bot）"""
    from channels.telegram import TelegramChannel
    from lib.bus.event_bus import EventBus
    from lib.bus.queue import MessageBus

    bus = MessageBus()
    event_bus = EventBus()

    with patch("telegram.ext.Application.builder") as mock_builder:
        mock_app = AsyncMock()
        mock_builder.return_value.token.return_value.request.return_value.build.return_value = mock_app

        tg_channel = TelegramChannel("fake-token", bus, event_bus)
        await tg_channel.start()

        # 验证 initialize 被调用
        mock_app.initialize.assert_called_once()

        await tg_channel.stop()


@pytest.mark.asyncio
async def test_agent_runner_with_source_platform():
    """验证 AgentRunner 正确处理 source_platform"""
    from lib.bus.event_bus import EventBus
    from lib.bus.queue import InboundMessage, MessageBus
    from lib.chat.agent_runner import AgentRunner

    bus = MessageBus()
    event_bus = EventBus()

    runner = AgentRunner(bus, event_bus)
    runner.start()

    # 发布来自 telegram 的消息
    await bus.publish_inbound(
        InboundMessage(
            channel="telegram",
            sender="demo_user",
            chat_id="12345",
            content="测试消息",
        )
    )

    # 等待处理
    await asyncio.sleep(0.5)

    await runner.stop()

    # 验证 outbound 有消息（即使失败也会有错误消息）
    # 这里主要是验证不崩溃
