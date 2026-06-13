"""Provider 插件加载器 — 扫描内置插件 + 用户插件目录。

目录结构：
    lib/memory/builtins/<name>/plugin.yaml + __init__.py
    ~/.lumen/plugins/memory/<name>/plugin.yaml + __init__.py

用户插件同名时覆盖内置插件，方便本地调试和扩展。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml

from core.config import USER_DATA_DIR
from lib.memory.provider import MemoryProvider
from shared.logging import get_logger

logger = get_logger(__name__)

_BUILTIN_PLUGINS_DIR = Path(__file__).parent / "builtins"
_USER_PLUGINS_DIR = USER_DATA_DIR / "plugins" / "memory"


def _ensure_plugins_dir(plugins_dir: Path | None = None) -> None:
    (plugins_dir or _USER_PLUGINS_DIR).mkdir(parents=True, exist_ok=True)


def _discover_in_directory(directory: Path) -> dict[str, type[MemoryProvider]]:
    """扫描单个目录，返回 name -> ProviderClass 映射。"""
    result: dict[str, type[MemoryProvider]] = {}
    if not directory.exists():
        return result

    for subdir in directory.iterdir():
        if not subdir.is_dir():
            continue

        plugin_yaml = subdir / "plugin.yaml"
        init_py = subdir / "__init__.py"

        if not plugin_yaml.exists() or not init_py.exists():
            logger.debug("跳过不完整插件目录", dir=subdir.name)
            continue

        try:
            with plugin_yaml.open("r", encoding="utf-8") as f:
                manifest = yaml.safe_load(f) or {}

            name = manifest.get("name", subdir.name)
            class_name = manifest.get("class", "Provider")

            # 动态 import
            module_name = f"lumen_memory_plugin_{directory.name}_{name}"
            spec = importlib.util.spec_from_file_location(module_name, init_py)
            if spec is None or spec.loader is None:
                logger.warning("无法加载插件", name=name, path=str(init_py))
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            # 按 class 字段查找
            ProviderClass = getattr(module, class_name, None)

            # 回退：查找任意 MemoryProvider 子类
            if ProviderClass is None:
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, MemoryProvider) and attr is not MemoryProvider:
                        ProviderClass = attr
                        break

            if ProviderClass is None:
                logger.warning("插件中未找到 Provider 类", name=name, path=str(init_py))
                continue

            result[name] = ProviderClass
            logger.info(
                "发现记忆插件",
                name=name,
                version=manifest.get("version", "?"),
                source=directory.name,
            )

        except Exception as exc:
            logger.warning("加载插件失败", name=subdir.name, error=str(exc))
            continue

    return result


def discover_providers(
    builtin_dir: Path | None = None,
    plugins_dir: Path | None = None,
) -> dict[str, type[MemoryProvider]]:
    """扫描内置插件目录和用户插件目录，返回合并后的 name -> ProviderClass 映射。

    用户插件同名时覆盖内置插件。
    """
    builtin_dir = builtin_dir or _BUILTIN_PLUGINS_DIR
    plugins_dir = plugins_dir or _USER_PLUGINS_DIR
    _ensure_plugins_dir(plugins_dir)

    # 先扫描内置，再扫描用户，实现覆盖
    result = _discover_in_directory(builtin_dir)
    user_providers = _discover_in_directory(plugins_dir)
    result.update(user_providers)
    return result


def discover_builtin_providers(
    builtin_dir: Path | None = None,
) -> dict[str, type[MemoryProvider]]:
    """仅扫描内置插件目录。"""
    return _discover_in_directory(builtin_dir or _BUILTIN_PLUGINS_DIR)


def discover_user_providers(
    plugins_dir: Path | None = None,
) -> dict[str, type[MemoryProvider]]:
    """仅扫描用户插件目录。"""
    plugins_dir = plugins_dir or _USER_PLUGINS_DIR
    _ensure_plugins_dir(plugins_dir)
    return _discover_in_directory(plugins_dir)


def load_provider(
    name: str,
    config: dict[str, Any] | None = None,
    builtin_dir: Path | None = None,
    plugins_dir: Path | None = None,
) -> MemoryProvider | None:
    """按 name 加载并实例化 provider，config 作为 **kwargs 传入构造函数。

    返回实例或 None（未找到/加载失败）。
    """
    discovered = discover_providers(builtin_dir=builtin_dir, plugins_dir=plugins_dir)
    ProviderClass = discovered.get(name)
    if ProviderClass is None:
        logger.warning("未找到 provider", name=name)
        return None

    try:
        config = config or {}
        instance = ProviderClass(**config)
        logger.info("Provider 实例化成功", name=name, config_keys=list(config.keys()))
        return instance
    except Exception as exc:
        logger.error("Provider 实例化失败", name=name, error=str(exc))
        return None
