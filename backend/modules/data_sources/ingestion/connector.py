"""外部数据源连接器基类与数据模型。"""

from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawBytes:
    """Connector 输出的原始字节。"""

    data_source_id: str
    external_id: str
    uri: str
    content_bytes: bytes
    mime_type: str | None
    metadata: dict[str, Any]
    last_modified: float
    user_id: str = "demo_user"

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content_bytes).hexdigest()


@dataclass
class DocumentSection:
    """结构化文档的章节。"""

    level: int  # 标题层级 (h1=1, h2=2...)
    title: str
    content: str
    start_line: int
    end_line: int


@dataclass
class StructuredDocument:
    """Parser 输出的结构化文档。"""

    data_source_id: str
    external_id: str
    uri: str
    title: str
    content: str  # 纯文本正文
    sections: list[DocumentSection]  # 文档结构大纲
    metadata: dict[str, Any]  # frontmatter + file stat
    wiki_links: list[str]  # 内部 WikiLinks
    external_links: list[str]  # 外部 URL 链接
    content_hash: str
    user_id: str = "demo_user"  # 从 RawBytes / DataSource 传递


# ── Phase 1 兼容：RawDocument 保留但标记为 deprecated ──


@dataclass
class RawDocument:
    """从外部数据源读取的原始文档（Phase 1 兼容，即将废弃）。"""

    user_id: str  # 当前用户（如 "demo_user"）
    data_source_id: str  # 用户建立的连接 ID（如 "ds_xxx"）
    connector_type: str  # "local_folder" / "web_url" / "github_repo"
    external_id: str  # 源系统内唯一 ID（文件绝对路径、URL、GitHub path）
    uri: str  # 可展示引用地址（file:///...、https://...）
    title: str  # 文档标题（用于 Agent 和前端展示）
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


class DataSourceConnector(ABC):
    """外部数据源连接器抽象基类。

    每种数据源（filesystem / github / web）实现此接口，
    核心 Pipeline 不感知具体来源。
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """数据源唯一标识。"""

    @property
    @abstractmethod
    def data_source_id(self) -> str:
        """数据源连接 ID（如 ds_xxx），用于 DB 关联。"""

    @abstractmethod
    def is_configured(self) -> bool:
        """是否已配置（有效的路径 / token）。"""

    @abstractmethod
    async def scan(self) -> AsyncIterator[RawBytes]:  # type: ignore[override]
        """全量扫描，返回所有文档的异步迭代器。启动时和手动触发时调用。"""

    @abstractmethod
    def start_watching(
        self,
        on_change: Callable[[RawBytes], Coroutine],
        on_delete: Callable[[str, str], Coroutine],
        *,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """启动增量监听。
        Args:
            on_change: 文件新增/修改时的回调，参数为 RawBytes。
            on_delete: 文件删除时的回调，参数为 (data_source_id, external_id)。
            loop: 主线程的事件循环，由调用方显式传入。
        """

    @abstractmethod
    def stop_watching(self) -> None:
        """停止监听。"""
