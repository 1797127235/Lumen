"""DocumentIndexProvider 工厂 — 根据配置创建 Provider 实例。"""

from __future__ import annotations

from backend.core.logging import get_logger
from backend.modules.data_sources.ingestion.document_index_provider import DocumentIndexProvider
from backend.modules.data_sources.ingestion.providers.null import NullProvider

logger = get_logger(__name__)

_PROVIDER_REGISTRY: dict[str, type[DocumentIndexProvider]] = {}


def _register_providers() -> None:
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
    if not _PROVIDER_REGISTRY:
        _register_providers()

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
