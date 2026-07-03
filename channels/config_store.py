"""Channel 配置持久化。

读写 ~/.lumen/config.json["channels"]。
"""

from __future__ import annotations

import os
from typing import Any

from core.config import get_settings, load_user_config, save_user_config
from shared.logging import get_logger

from .models import ChannelConfig

logger = get_logger(__name__)

_CONFIG_KEY = "channels"


def _ensure_list(value: Any) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def load_channel_configs() -> list[ChannelConfig]:
    """读取所有 channel 配置（enabled=false 也会被返回，由调用方过滤）。"""
    raw = load_user_config().get(_CONFIG_KEY)
    return [ChannelConfig(**item) for item in _ensure_list(raw)]


def save_channel_configs(configs: list[ChannelConfig]) -> None:
    """覆盖保存整个 channel 配置列表。"""
    data = {_CONFIG_KEY: [cfg.model_dump() for cfg in configs]}
    save_user_config(data)


def add_channel_config(config: ChannelConfig) -> None:
    """添加或覆盖同名 channel 配置。"""
    configs = load_channel_configs()
    configs = [c for c in configs if c.name != config.name]
    configs.append(config)
    save_channel_configs(configs)


def remove_channel_config(name: str) -> bool:
    """删除指定 channel 配置，返回是否删除成功。"""
    configs = load_channel_configs()
    new_configs = [c for c in configs if c.name != name]
    if len(new_configs) == len(configs):
        return False
    save_channel_configs(new_configs)
    return True


def update_channel_config(name: str, patch: dict[str, Any]) -> ChannelConfig | None:
    """部分更新指定 channel 配置。"""
    configs = load_channel_configs()
    for i, cfg in enumerate(configs):
        if cfg.name == name:
            data = cfg.model_dump()
            data.update({k: v for k, v in patch.items() if k in ChannelConfig.model_fields})
            configs[i] = ChannelConfig(**data)
            save_channel_configs(configs)
            return configs[i]
    return None


def migrate_legacy_channel_config() -> bool:
    """从旧环境变量/Settings 迁移到 channels 配置。

    仅在 config.json 中不存在 channels 且存在旧配置时执行一次。
    返回是否发生了迁移。
    """
    cfg = load_user_config()
    if _CONFIG_KEY in cfg:
        return False

    settings = get_settings()
    channels: list[dict[str, Any]] = []

    # Web channel（默认启用，除非 LUMEN_ENABLE_WEB=0）
    enable_web = os.getenv("LUMEN_ENABLE_WEB", "1") != "0" and getattr(settings, "enable_web", True)
    channels.append(
        {
            "name": "web",
            "provider_type": "web",
            "enabled": enable_web,
            "config": {},
        }
    )

    # Telegram channel
    telegram_token = getattr(settings, "telegram_bot_token", None) or os.getenv("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        channels.append(
            {
                "name": "telegram",
                "provider_type": "telegram",
                "enabled": True,
                "config": {"bot_token": telegram_token},
            }
        )

    if not channels:
        return False

    save_user_config({_CONFIG_KEY: channels})
    logger.info("旧 channel 配置已迁移到 channels", channels=[c["name"] for c in channels])
    return True
