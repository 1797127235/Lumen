"""Memory Provider 插件加载器 — 扫描内置插件 + 用户插件目录。

目录结构：
    lib/memory/builtins/<name>/plugin.yaml + __init__.py
    ~/.lumen/plugins/memory/<name>/plugin.yaml + __init__.py

用户插件同名时覆盖内置插件，方便本地调试和扩展。
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from core.config import USER_DATA_DIR
from lib.memory.provider import MemoryProvider
from lib.plugins.loader import (
    discover_in_directory,
    discover_plugins,
    load_plugin_instance,
)
from shared.logging import get_logger

# 兼容旧调用：_discover_in_directory(tmp_path) 不需要传 base_class
_discover_in_directory = partial(
    discover_in_directory,
    base_class=MemoryProvider,
    default_class_name="Provider",
)

logger = get_logger(__name__)

_BUILTIN_PLUGINS_DIR = Path(__file__).parent / "builtins"
_USER_PLUGINS_DIR = USER_DATA_DIR / "plugins" / "memory"


def _ensure_plugins_dir(plugins_dir: Path | None = None) -> None:
    (plugins_dir or _USER_PLUGINS_DIR).mkdir(parents=True, exist_ok=True)


def discover_providers(
    builtin_dir: Path | None = None,
    plugins_dir: Path | None = None,
) -> dict[str, type[MemoryProvider]]:
    """扫描内置插件目录和用户插件目录，返回合并后的 name -> ProviderClass 映射。

    用户插件同名时覆盖内置插件。
    """
    return discover_plugins(
        builtin_dir=builtin_dir or _BUILTIN_PLUGINS_DIR,
        user_dir=plugins_dir or _USER_PLUGINS_DIR,
        base_class=MemoryProvider,
        default_class_name="Provider",
    )


def discover_builtin_providers(
    builtin_dir: Path | None = None,
) -> dict[str, type[MemoryProvider]]:
    """仅扫描内置插件目录。"""
    return discover_plugins(
        builtin_dir=builtin_dir or _BUILTIN_PLUGINS_DIR,
        user_dir=None,
        base_class=MemoryProvider,
        default_class_name="Provider",
    )


def discover_user_providers(
    plugins_dir: Path | None = None,
) -> dict[str, type[MemoryProvider]]:
    """仅扫描用户插件目录。"""
    return discover_plugins(
        builtin_dir=None,
        user_dir=plugins_dir or _USER_PLUGINS_DIR,
        base_class=MemoryProvider,
        default_class_name="Provider",
    )


def load_provider(
    name: str,
    config: dict[str, Any] | None = None,
    builtin_dir: Path | None = None,
    plugins_dir: Path | None = None,
) -> MemoryProvider | None:
    """按 name 加载并实例化 provider，config 作为 **kwargs 传入构造函数。

    返回实例或 None（未找到/加载失败）。
    """
    return load_plugin_instance(
        name=name,
        builtin_dir=builtin_dir or _BUILTIN_PLUGINS_DIR,
        user_dir=plugins_dir or _USER_PLUGINS_DIR,
        base_class=MemoryProvider,
        config=config,
        default_class_name="Provider",
    )
