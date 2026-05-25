"""Provider 插件加载器 — 扫描 ~/.lumen/plugins/memory/ 发现外部 provider。

第一版：扫描目录 → 读 plugin.yaml → import __init__.py → 取 Provider 类 → 实例化。
不需要处理 pip 依赖安装。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

from core.config import USER_DATA_DIR
from lib.memory.provider import MemoryProvider
from shared.logging import get_logger

logger = get_logger(__name__)

_PLUGINS_DIR = USER_DATA_DIR / "plugins" / "memory"


def _ensure_plugins_dir() -> None:
    _PLUGINS_DIR.mkdir(parents=True, exist_ok=True)


def discover_providers(plugins_dir: Path | None = None) -> dict[str, type[MemoryProvider]]:
    """扫描插件目录，读取 plugin.yaml，import __init__.py。

    返回 name -> ProviderClass 映射。
    """
    plugins_dir = plugins_dir or _PLUGINS_DIR
    _ensure_plugins_dir()

    result: dict[str, type[MemoryProvider]] = {}

    if not plugins_dir.exists():
        return result

    for subdir in plugins_dir.iterdir():
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

            # 动态 import
            spec = importlib.util.spec_from_file_location(f"lumen_memory_plugin_{name}", init_py)
            if spec is None or spec.loader is None:
                logger.warning("无法加载插件", name=name, path=str(init_py))
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)

            # 查找 Provider 类
            ProviderClass = getattr(module, "Provider", None)
            if ProviderClass is None:
                # 尝试找任何继承 MemoryProvider 的类
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, MemoryProvider) and attr is not MemoryProvider:
                        ProviderClass = attr
                        break

            if ProviderClass is None:
                logger.warning("插件中未找到 Provider 类", name=name, path=str(init_py))
                continue

            result[name] = ProviderClass
            logger.info("发现记忆插件", name=name, version=manifest.get("version", "?"))

        except Exception as exc:
            logger.warning("加载插件失败", name=subdir.name, error=str(exc))
            continue

    return result


def load_provider(name: str, plugins_dir: Path | None = None) -> MemoryProvider | None:
    """按 name 加载并实例化 provider。

    返回实例或 None（未找到/加载失败）。
    """
    discovered = discover_providers(plugins_dir)
    ProviderClass = discovered.get(name)
    if ProviderClass is None:
        logger.warning("未找到 provider", name=name)
        return None

    try:
        instance = ProviderClass()
        logger.info("Provider 实例化成功", name=name)
        return instance
    except Exception as exc:
        logger.error("Provider 实例化失败", name=name, error=str(exc))
        return None
