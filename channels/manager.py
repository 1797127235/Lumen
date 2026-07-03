"""ChannelManager — channel provider 发现、加载、生命周期编排。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import USER_DATA_DIR
from lib.bus.event_bus import EventBus
from lib.bus.queue import MessageBus
from lib.plugins.loader import discover_plugins, load_plugin_instance
from shared.logging import get_logger

from .base import BaseChannel
from .models import ChannelConfig
from .provider import ChannelProvider

logger = get_logger(__name__)

BUILTIN_PLUGINS_DIR = Path(__file__).parent / "builtins"
USER_PLUGINS_DIR = USER_DATA_DIR / "plugins" / "channels"


class ChannelManager:
    """Channel 插件管理器。

    负责：
    - 扫描内置 + 用户 channel provider 插件
    - 根据配置实例化并启动 channel
    - 关闭时统一停止所有 channel
    """

    def __init__(self, bus: MessageBus, event_bus: EventBus) -> None:
        self._bus = bus
        self._event_bus = event_bus
        self._providers: dict[str, type[ChannelProvider]] | None = None

    def discover_providers(
        self,
        builtin_dir: Path | None = None,
        user_dir: Path | None = None,
    ) -> dict[str, type[ChannelProvider]]:
        """扫描内置和用户 channel provider 目录。"""
        if self._providers is None:
            self._providers = discover_plugins(
                builtin_dir=builtin_dir or BUILTIN_PLUGINS_DIR,
                user_dir=user_dir or USER_PLUGINS_DIR,
                base_class=ChannelProvider,
                default_class_name="Provider",
            )
        return self._providers

    def list_discovered_provider_types(self) -> list[str]:
        """返回已发现的 provider 类型名列表。"""
        return list(self.discover_providers().keys())

    async def start_channels(self, configs: list[ChannelConfig]) -> list[BaseChannel]:
        """根据配置启动所有 enabled 的 channel。

        单个 channel 启动失败只记录日志，不阻塞其他 channel。
        """
        providers = self.discover_providers()
        channels: list[BaseChannel] = []

        for cfg in configs:
            if not cfg.enabled:
                logger.info("Channel 已禁用，跳过", name=cfg.name, provider_type=cfg.provider_type)
                continue

            ProviderClass = providers.get(cfg.provider_type)
            if ProviderClass is None:
                logger.warning(
                    "未找到 channel provider",
                    name=cfg.name,
                    provider_type=cfg.provider_type,
                )
                continue

            try:
                provider = ProviderClass()
                build_config = dict(cfg.config)
                build_config.setdefault("instance_name", cfg.name)
                channel = provider.build(
                    build_config,
                    bus=self._bus,
                    event_bus=self._event_bus,
                )
                await channel.start()
                channels.append(channel)
                logger.info(
                    "Channel 已启动",
                    name=cfg.name,
                    provider_type=cfg.provider_type,
                    channel=channel.name,
                )
            except Exception as exc:
                logger.error(
                    "Channel 启动失败",
                    name=cfg.name,
                    provider_type=cfg.provider_type,
                    error=str(exc),
                )

        return channels

    async def stop_channels(self, channels: list[BaseChannel]) -> None:
        """停止所有 channel，单个失败不影响其他。"""
        for channel in channels:
            try:
                await channel.stop()
                logger.info("Channel 已停止", channel=channel.name, instance=channel.instance_name)
            except Exception as exc:
                logger.warning(
                    "Channel 停止失败",
                    channel=channel.name,
                    instance=channel.instance_name,
                    error=str(exc),
                )

    def load_provider_instance(
        self,
        provider_type: str,
        config: dict[str, Any] | None = None,
    ) -> ChannelProvider | None:
        """按 provider_type 加载一个 provider 实例（用于测试连通性等）。"""
        return load_plugin_instance(
            name=provider_type,
            builtin_dir=BUILTIN_PLUGINS_DIR,
            user_dir=USER_PLUGINS_DIR,
            base_class=ChannelProvider,
            config=config,
            default_class_name="Provider",
        )
