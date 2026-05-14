"""DocumentIndexProvider 工厂 — 根据配置创建 Provider 实例。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from backend.core.logging import get_logger
from backend.modules.data_sources.ingestion.document_index_provider import DocumentIndexProvider
from backend.modules.data_sources.ingestion.providers.null import NullProvider

logger = get_logger(__name__)

_PROVIDER_REGISTRY: dict[str, type[DocumentIndexProvider]] = {}


def _discover_providers() -> None:
    """从 plugins/memory/ 目录自动发现 Provider 实现。

    使用 importlib.util.spec_from_file_location 直接加载文件，
    避免对 sys.path 的依赖。

    目录结构：
    backend/modules/data_sources/ingestion/plugins/memory/
        cognee/
            __init__.py  # 导出 Provider = CogneeProvider
        lancedb/
            __init__.py  # 导出 Provider = LanceDBProvider
    """
    plugins_dir = Path(__file__).parent / "plugins" / "memory"
    if not plugins_dir.exists():
        return

    for provider_dir in plugins_dir.iterdir():
        if not provider_dir.is_dir():
            continue

        init_file = provider_dir / "__init__.py"
        if not init_file.exists():
            continue

        module_name = f"lumen.plugins.memory.{provider_dir.name}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(init_file))
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            # 临时注入 sys.modules 避免相对导入问题
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            provider_cls = getattr(module, "Provider", None)
            if provider_cls and issubclass(provider_cls, DocumentIndexProvider):
                _PROVIDER_REGISTRY[provider_cls.provider_name()] = provider_cls
        except Exception as e:
            logger.warning(f"Failed to load provider {provider_dir.name}: {e}")


def _register_builtin_providers() -> None:
    """注册内置 Provider（类级别，无需实例化）。"""
    from backend.modules.data_sources.ingestion.providers.cognee import CogneeProvider
    from backend.modules.data_sources.ingestion.providers.hrr import HRRProvider
    from backend.modules.data_sources.ingestion.providers.lancedb import LanceDBProvider

    _PROVIDER_REGISTRY[CogneeProvider.provider_name()] = CogneeProvider
    _PROVIDER_REGISTRY[LanceDBProvider.provider_name()] = LanceDBProvider
    _PROVIDER_REGISTRY[HRRProvider.provider_name()] = HRRProvider
    _PROVIDER_REGISTRY[NullProvider.provider_name()] = NullProvider


def create_document_index_provider(config: dict | None = None) -> DocumentIndexProvider:
    """根据配置创建 DocumentIndexProvider 实例。

    优先级：
      1. config["document_index_provider"] 显式指定
      2. 检测可用后端（Cognee → Null）
    """
    # 首次调用时注册内置 + 发现插件
    if not _PROVIDER_REGISTRY:
        _register_builtin_providers()
        _discover_providers()

    backend = (config or {}).get("document_index_provider", "auto")

    if backend == "auto":
        # 自动检测：优先 Cognee → LanceDB → HRR → Null
        for name in ("cognee", "lancedb", "hrr", "null"):
            cls = _PROVIDER_REGISTRY.get(name)
            if cls and cls.is_available():
                backend = name
                break
        else:
            backend = "null"

    provider_cls = _PROVIDER_REGISTRY.get(backend)
    if not provider_cls:
        logger.warning(f"Unknown provider {backend}, falling back to null")
        provider_cls = _PROVIDER_REGISTRY.get("null", NullProvider)

    provider = provider_cls()
    provider.initialize()
    logger.info("document_index_provider.initialized", provider=provider.name)
    return provider
