"""Memory Provider 配置持久化。

读写 ~/.lumen/config.json["memory_providers"]。
"""

from __future__ import annotations

import os
from typing import Any

from core.config import load_user_config, save_user_config
from lib.memory.models import MemoryProviderConfig
from shared.logging import get_logger

logger = get_logger(__name__)

_CONFIG_KEY = "memory_providers"


def _ensure_list(value: Any) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def load_memory_provider_configs() -> list[MemoryProviderConfig]:
    """读取所有记忆 Provider 配置（仅 enabled 为 true 也会被返回，由调用方过滤）。"""
    raw = load_user_config().get(_CONFIG_KEY)
    return [MemoryProviderConfig(**item) for item in _ensure_list(raw)]


def get_enabled_external_providers() -> list[MemoryProviderConfig]:
    """返回所有 enabled=True 的外部 provider 配置。

    设计约束：最多 1 个外部 provider 并存。
    本函数供 API 路由在 create/update 时校验是否已有其他 enabled 配置。
    builtin 不走 config，不在此列。
    """
    return [cfg for cfg in load_memory_provider_configs() if cfg.enabled]


def save_memory_provider_configs(configs: list[MemoryProviderConfig]) -> None:
    """覆盖保存整个记忆 Provider 配置列表。"""
    data = {key: val for key, val in (("memory_providers", [cfg.model_dump() for cfg in configs]),) if val}
    save_user_config(data)


def add_memory_provider_config(config: MemoryProviderConfig) -> None:
    """添加或覆盖同名配置。"""
    configs = load_memory_provider_configs()
    configs = [c for c in configs if c.name != config.name]
    configs.append(config)
    save_memory_provider_configs(configs)


def remove_memory_provider_config(name: str) -> bool:
    """删除指定配置，返回是否删除成功。"""
    configs = load_memory_provider_configs()
    new_configs = [c for c in configs if c.name != name]
    if len(new_configs) == len(configs):
        return False
    save_memory_provider_configs(new_configs)
    return True


def update_memory_provider_config(name: str, patch: dict[str, Any]) -> MemoryProviderConfig | None:
    """部分更新指定配置。"""
    configs = load_memory_provider_configs()
    for i, cfg in enumerate(configs):
        if cfg.name == name:
            data = cfg.model_dump()
            data.update({k: v for k, v in patch.items() if k in MemoryProviderConfig.model_fields})
            configs[i] = MemoryProviderConfig(**data)
            save_memory_provider_configs(configs)
            return configs[i]
    return None


def migrate_honcho_enabled() -> bool:
    """从旧的 honcho_enabled / HONCHO_API_KEY 迁移到 memory_providers 配置。

    仅在 config.json 中不存在 memory_providers 且存在旧配置时执行一次。
    返回是否发生了迁移。
    """
    cfg = load_user_config()
    if _CONFIG_KEY in cfg:
        return False

    honcho_enabled = cfg.get("honcho_enabled")
    api_key = os.getenv("HONCHO_API_KEY", "")

    if honcho_enabled is False:
        # 用户明确关闭 Honcho，写入空列表避免反复检查
        save_user_config({_CONFIG_KEY: []})
        logger.info("honcho_enabled=false 已迁移为空的 memory_providers")
        return True

    if honcho_enabled is True or api_key:
        config = MemoryProviderConfig(
            name="honcho",
            provider_type="honcho",
            enabled=True,
            config={
                "api_key": api_key,
                "workspace_id": os.getenv("HONCHO_WORKSPACE_ID", "lumen"),
                "environment": os.getenv("HONCHO_ENVIRONMENT", "production"),
            },
        )
        save_user_config({_CONFIG_KEY: [config.model_dump()]})
        logger.info("honcho_enabled / HONCHO_API_KEY 已迁移到 memory_providers")
        return True

    # 没有旧 honcho 配置，不写空列表，保持 config.json 干净
    return False
