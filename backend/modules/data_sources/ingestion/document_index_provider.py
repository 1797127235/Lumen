"""文档索引提供者 — 可插拔的记忆后端抽象。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderHit:
    """Provider 召回的单条结果。

    所有 Provider 实现的 prefetch() 必须返回 list[ProviderHit]，
    不再依赖字符串格式约定和正则解析。
    """

    doc_id: str
    """文档唯一标识，与 sync_document 的 doc_id 对应。"""

    content: str
    """召回的文本片段（已截断到合理长度）。"""

    score: float = 0.0
    """相似度分数（0.0 ~ 1.0），不支持时填 0.0。"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """原始元数据，供调用方按需使用。"""


class DocumentIndexProvider(ABC):
    """记忆提供者抽象基类。所有具体后端必须继承此类。

    接口极简：主循环只调 prefetch() 和 sync_document()，
    完全不知道背后是 Cognee、LanceDB 还是 HRR。
    分块策略由 Provider 内部决定，外部不感知。
    """

    @classmethod
    @abstractmethod
    def provider_name(cls) -> str:
        """Provider 名称标识（类级别，无需实例化）。"""

    @property
    def name(self) -> str:
        """实例属性兼容：委托给类方法。"""
        return self.provider_name()

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """检查依赖是否已安装（如 cognee、lancedb）。类级别，无需实例化。"""

    @abstractmethod
    def initialize(self) -> None:
        """初始化后端（建表、建索引等）。"""

    @abstractmethod
    async def prefetch(self, query: str) -> list[ProviderHit]:
        """召回相关内容，返回结构化结果列表。

        无相关结果时返回空列表。
        调用方（search.py）负责将结果转换为 MemoryItem，不再依赖字符串格式。
        """

    @abstractmethod
    async def sync_document(
        self,
        content: str,
        doc_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """保存文档内容。分块策略由 Provider 内部决定，外部不感知。"""

    def get_tool_schemas(self) -> list[dict]:
        """Provider 可以暴露自己的工具给 Agent。默认空列表。"""
        return []

    async def clear(self) -> bool:
        """清空所有索引数据。返回是否成功。默认空操作。"""
        return True

    async def delete_document(self, doc_id: str) -> bool:
        """删除指定文档的索引。返回是否成功。默认空操作。"""
        return True

    async def handle_tool_call(self, name: str, args: dict) -> str:
        """处理 Agent 的工具调用。默认抛异常。"""
        raise NotImplementedError(f"Tool {name} not supported by {self.name}")
