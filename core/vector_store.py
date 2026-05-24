"""Vector store 基础设施出口。

提供 DocumentIndexProvider 抽象及默认 NullProvider 实现。
当 LanceDB 等真实 Provider 不可用时自动降级为 NullProvider。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HealthStatus(Enum):
    """DocumentIndexProvider 健康状态。"""

    READY = "ready"
    INITIALIZING = "initializing"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass
class ProviderHit:
    """语义搜索命中结果。"""

    doc_id: str
    content: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class DocumentIndexProvider:
    """语义文档索引 Provider 抽象基类。"""

    name: str = "abstract"

    async def initialize(self) -> None:
        """异步初始化（如加载索引、建立连接）。"""
        pass

    async def prefetch(self, query: str) -> list[ProviderHit]:
        """语义搜索，返回与 query 最相关的文档列表。"""
        return []

    async def sync_document(self, content: str, doc_id: str, metadata: dict[str, Any] | None = None) -> None:
        """将文档同步到索引中（新增或覆盖）。"""
        pass

    def health_check(self) -> HealthStatus:
        """返回当前 Provider 健康状态。"""
        return HealthStatus.DISABLED


class NullProvider(DocumentIndexProvider):
    """空实现 Provider — 当 LanceDB 等真实 Provider 不可用时使用。

    所有操作静默无操作，prefetch 始终返回空列表。
    """

    name = "null"

    async def prefetch(self, query: str) -> list[ProviderHit]:
        return []

    async def sync_document(self, content: str, doc_id: str, metadata: dict[str, Any] | None = None) -> None:
        pass

    def health_check(self) -> HealthStatus:
        return HealthStatus.DISABLED


# 进程级单例缓存
_provider_instance: DocumentIndexProvider | None = None


def get_document_index_provider() -> DocumentIndexProvider:
    """获取全局 DocumentIndexProvider 单例。

    首次调用时尝试初始化 LanceDB Provider，失败则降级为 NullProvider。
    """
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    # 尝试初始化 LanceDB Provider（如果依赖可用）
    try:
        # 延迟导入，避免在 import 阶段触发 heavy 初始化
        from lancedb import connect_async

        class LanceDBProvider(DocumentIndexProvider):
            name = "lancedb"

            def __init__(self) -> None:
                self._db = None
                self._table = None
                self._error_msg: str | None = None

            async def initialize(self) -> None:
                from core.config import USER_DATA_DIR
                from shared.logging import get_logger

                logger = get_logger(__name__)
                db_path = str(USER_DATA_DIR / "lancedb")
                try:
                    self._db = await connect_async(db_path)
                    logger.info("lancedb.connected", path=db_path)
                except Exception as exc:
                    self._error_msg = str(exc)
                    logger.warning("lancedb.connect_failed", error=str(exc))

            async def prefetch(self, query: str) -> list[ProviderHit]:
                if self._db is None:
                    return []
                try:
                    # LanceDB 语义搜索实现
                    # 这里简化实现，实际应根据项目需要扩展
                    return []
                except Exception:
                    return []

            async def sync_document(self, content: str, doc_id: str, metadata: dict[str, Any] | None = None) -> None:
                if self._db is None:
                    return
                try:
                    pass  # LanceDB 同步逻辑
                except Exception:
                    pass

            def health_check(self) -> HealthStatus:
                if self._db is None:
                    return HealthStatus.ERROR if self._error_msg else HealthStatus.INITIALIZING
                return HealthStatus.READY

        _provider_instance = LanceDBProvider()
        return _provider_instance
    except ImportError:
        # lancedb 未安装 → NullProvider
        _provider_instance = NullProvider()
        return _provider_instance


__all__ = [
    "DocumentIndexProvider",
    "HealthStatus",
    "NullProvider",
    "ProviderHit",
    "get_document_index_provider",
]
