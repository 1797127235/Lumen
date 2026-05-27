"""应用生命周期 — 启动/关闭编排。"""

from __future__ import annotations

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.config import apply_user_config, get_settings
from core.db import Base, get_engine, init_db
from core.migrations import migrate_sqlite
from shared.logging import get_logger, setup_logging

logger = get_logger(__name__)


def _init_logging() -> None:
    settings = get_settings()
    setup_logging(
        json_logs=not settings.debug,
        log_level="DEBUG" if settings.debug else "INFO",
    )


async def _init_db() -> None:
    # 确保所有模型注册到 Base.metadata（自包含，不依赖 main.py 底部 import）
    from lib import model_registry  # noqa: F401

    init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if "sqlite" in str(engine.url):
            await migrate_sqlite(conn)


async def _shutdown(engine) -> None:
    await engine.dispose()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan — 启动初始化 + 关闭清理。"""
    _init_logging()

    await _init_db()

    # Hermes-Pure: 文件命名迁移（memory.md → MEMORY.md, about_you.md → USER.md）
    from core.migrations import migrate_md_files

    await migrate_md_files()

    applied = apply_user_config(get_settings())
    if applied:
        logger.info("config.json 覆盖", keys=list(applied.keys()))

    # Hermes-Pure: 语义索引补偿循环已移除（ProjectionManager 已删除）
    pass

    # 连接已配置的 MCP Servers
    with contextlib.suppress(Exception):
        from lib.tools.mcp.client_manager import get_mcp_manager

        await get_mcp_manager().connect_all()

    # ═══════════════════════════════════════════════════════════
    #  新增：MessageBus + EventBus + Channels + AgentRunner
    # ═══════════════════════════════════════════════════════════
    settings = get_settings()

    from lib.bus.event_bus import EventBus
    from lib.bus.queue import MessageBus
    from lib.channels.web import WebChannel
    from lib.chat.agent_runner import AgentRunner

    bus = MessageBus()
    event_bus = EventBus()

    # 启动 Channels（配置驱动）
    channels = []
    enable_web = os.getenv("LUMEN_ENABLE_WEB", "1") != "0"

    # WebChannel（配置驱动，默认启用）
    if enable_web and getattr(settings, "enable_web", True):
        web_channel = WebChannel(bus, event_bus)
        await web_channel.start()
        channels.append(web_channel)
        app.state.web_channel = web_channel
        logger.info("WebChannel enabled")

    # TelegramChannel（TELEGRAM_BOT_TOKEN 存在时启用）
    telegram_token = getattr(settings, "telegram_bot_token", None) or os.getenv("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        from lib.channels.telegram import TelegramChannel

        tg_channel = TelegramChannel(telegram_token, bus, event_bus)
        try:
            await tg_channel.start()
            channels.append(tg_channel)
            logger.info("TelegramChannel enabled")
        except Exception as e:
            logger.error("TelegramChannel 启动失败（网络或 Token 问题），Web 端不受影响: %s", e)

    # CLI TUI 由 lumen.py 通过 --mode cli 管理，不在此处启动
    # 独立运行: cd lib/channels/cli && bun run dev

    # 启动 AgentRunner
    runner = AgentRunner(bus, event_bus)
    runner.start()

    # 启动出站消息分发
    dispatch_task = asyncio.create_task(bus.dispatch_outbound())

    yield

    # ═══════════════════════════════════════════════════════════
    #  清理
    # ═══════════════════════════════════════════════════════════
    await runner.stop()
    dispatch_task.cancel()

    for channel in channels:
        await channel.stop()

    # 断开 MCP Servers
    with contextlib.suppress(Exception):
        from lib.tools.mcp.client_manager import get_mcp_manager

        await get_mcp_manager().disconnect_all()

    await _shutdown(get_engine())
