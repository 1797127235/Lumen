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

    # ── 主动行动能力:时间触发(scheduler)+ 事件触发(triggers)──
    # 在 MCP connect_all 之前初始化,确保 server 通知 handler 能解析到 TriggerManager。
    # 统一概念模型:两者都是「触发源 → 动作」;notify 动作共享同一条
    # 「注入 InboundMessage → AgentRunner → agent 经 Telegram 主动触达」路径。
    # 详见 lib/scheduler/__init__.py 与 lib/triggers/__init__.py 顶部 docstring。
    from lib.bus.queue import MessageBus, set_bus

    bus = MessageBus()  # 提前创建,MCP 通知转发与下方 Channels 共用同一实例
    set_bus(bus)  # 注册全局单例,主动送达层(delivery.py)经 get_bus() 取用

    sched_engine = None
    trigger_manager = None
    with contextlib.suppress(Exception):
        from lib.scheduler import get_scheduler_engine
        from lib.triggers import get_manager

        trigger_manager = get_manager(bus)
        await trigger_manager.start()
        sched_engine = get_scheduler_engine(bus)
        await sched_engine.start()
        logger.info("Proactive engines started (scheduler + triggers)")

    # 连接已配置的 MCP Servers(通知 handler 此刻可正确转发给 TriggerManager)
    try:
        from lib.tools.mcp.client_manager import get_mcp_manager

        await get_mcp_manager().connect_all()
    except BaseException:
        logger.debug("MCP server 连接失败，跳过")

    # 注册外部记忆 Provider（配置驱动 + 插件化）
    with contextlib.suppress(Exception):
        from lib.memory import discover_providers, get_memory_manager, load_provider
        from lib.memory.config_store import load_memory_provider_configs, migrate_honcho_enabled

        # 从旧 honcho_enabled / HONCHO_API_KEY 迁移一次
        migrate_honcho_enabled()

        manager = get_memory_manager()
        provider_configs = load_memory_provider_configs()
        discovered = discover_providers()

        for cfg in provider_configs:
            if not cfg.enabled:
                continue
            if cfg.provider_type not in discovered:
                logger.warning(
                    "未找到记忆 provider 插件",
                    name=cfg.name,
                    provider_type=cfg.provider_type,
                )
                continue
            provider = load_provider(cfg.provider_type, config=cfg.config)
            if provider is None:
                continue
            if await provider.is_available():
                manager.add_provider(provider, instance_name=cfg.name)
                logger.info(
                    "记忆 provider 已注册",
                    name=cfg.name,
                    provider_type=cfg.provider_type,
                )
            else:
                logger.warning(
                    "记忆 provider 不可用，跳过",
                    name=cfg.name,
                    provider_type=cfg.provider_type,
                )

    # ── 初始化 SessionManager（Agent 历史层）──
    from lib.session import init_session_manager

    init_session_manager()
    logger.info("SessionManager initialized")

    # ═══════════════════════════════════════════════════════════
    #  新增：MessageBus + EventBus + Channels + AgentRunner
    # ═══════════════════════════════════════════════════════════
    settings = get_settings()

    from channels.web.web import WebChannel
    from lib.bus.event_bus import EventBus
    from lib.chat.agent_runner import AgentRunner

    # bus 已在上方(MCP 连接前)创建,此处只补 EventBus
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
        from channels.telegram import TelegramChannel

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

    # 启动记忆定期整理（过期 transient / stale intent）
    from lib.memory.housekeeping import MemoryHousekeeper

    housekeeper = MemoryHousekeeper()
    housekeeper.start()

    yield

    # ═══════════════════════════════════════════════════════════
    #  清理
    # ═══════════════════════════════════════════════════════════

    await housekeeper.stop()

    # 关闭 SessionManager
    with contextlib.suppress(Exception):
        from lib.session import get_session_manager

        get_session_manager().close()

    await runner.stop()
    dispatch_task.cancel()

    # 关闭主动行动引擎(与启动顺序相反)
    with contextlib.suppress(Exception):
        if trigger_manager is not None:
            await trigger_manager.stop()
    with contextlib.suppress(Exception):
        if sched_engine is not None:
            await sched_engine.stop()

    for channel in channels:
        await channel.stop()

    # 断开 MCP Servers
    with contextlib.suppress(Exception):
        from lib.tools.mcp.client_manager import get_mcp_manager

        await get_mcp_manager().disconnect_all()

    await _shutdown(get_engine())
