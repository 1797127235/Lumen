"""通用插件加载器。

扫描内置插件目录 + 用户插件目录，动态加载 plugin.yaml + __init__.py。
用户插件同名时覆盖内置插件。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, TypeVar

import yaml

from shared.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def _ensure_plugins_dir(plugins_dir: Path) -> None:
    plugins_dir.mkdir(parents=True, exist_ok=True)


def discover_in_directory(
    directory: Path,
    *,
    base_class: type[T],
    default_class_name: str = "Provider",
) -> dict[str, type[T]]:
    """扫描单个目录，返回 name -> PluginClass 映射。"""
    result: dict[str, type[T]] = {}
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
            class_name = manifest.get("class", default_class_name)

            module_name = f"lumen_plugin_{directory.name}_{name}"
            spec = importlib.util.spec_from_file_location(module_name, init_py)
            if spec is None or spec.loader is None:
                logger.warning("无法加载插件", name=name, path=str(init_py))
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            PluginClass: type[T] | None = getattr(module, class_name, None)

            # 回退：查找任意 base_class 子类
            if PluginClass is None:
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, base_class) and attr is not base_class:
                        PluginClass = attr
                        break

            if PluginClass is None:
                logger.warning("插件中未找到目标类", name=name, path=str(init_py), expected=base_class.__name__)
                continue

            result[name] = PluginClass
            logger.info(
                "发现插件",
                name=name,
                version=manifest.get("version", "?"),
                source=directory.name,
                plugin_class=PluginClass.__name__,
            )

        except Exception as exc:
            logger.warning("加载插件失败", name=subdir.name, error=str(exc))
            continue

    return result


def discover_plugins(
    builtin_dir: Path | None = None,
    user_dir: Path | None = None,
    *,
    base_class: type[T],
    default_class_name: str = "Provider",
) -> dict[str, type[T]]:
    """扫描内置插件目录和用户插件目录，返回合并后的 name -> PluginClass 映射。

    用户插件同名时覆盖内置插件。
    """
    if user_dir is not None:
        _ensure_plugins_dir(user_dir)

    # 先扫描内置，再扫描用户，实现覆盖
    result: dict[str, type[T]] = {}
    if builtin_dir is not None:
        result.update(
            discover_in_directory(
                builtin_dir,
                base_class=base_class,
                default_class_name=default_class_name,
            )
        )
    if user_dir is not None:
        result.update(
            discover_in_directory(
                user_dir,
                base_class=base_class,
                default_class_name=default_class_name,
            )
        )
    return result


def load_plugin_instance(
    name: str,
    builtin_dir: Path | None = None,
    user_dir: Path | None = None,
    *,
    base_class: type[T],
    config: dict[str, Any] | None = None,
    default_class_name: str = "Provider",
) -> T | None:
    """按 name 加载并实例化插件，config 作为 **kwargs 传入构造函数。

    返回实例或 None（未找到/加载失败）。
    """
    discovered = discover_plugins(
        builtin_dir=builtin_dir,
        user_dir=user_dir,
        base_class=base_class,
        default_class_name=default_class_name,
    )
    PluginClass = discovered.get(name)
    if PluginClass is None:
        logger.warning("未找到插件", name=name)
        return None

    try:
        config = config or {}
        instance = PluginClass(**config)
        logger.info("插件实例化成功", name=name, config_keys=list(config.keys()))
        return instance
    except Exception as exc:
        logger.error("插件实例化失败", name=name, error=str(exc))
        return None
