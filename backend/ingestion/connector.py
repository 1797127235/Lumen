"""外部数据源连接器基类。"""

from __future__ import annotations

import asyncio
import hashlib
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawDocument:
    """从外部数据源读取的原始文档。"""

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

    @abstractmethod
    def is_configured(self) -> bool:
        """是否已配置（有效的路径 / token）。"""

    @abstractmethod
    async def scan(self) -> AsyncIterator[RawDocument]:
        """全量扫描，返回所有文档的异步迭代器。启动时和手动触发时调用。"""

    @abstractmethod
    def start_watching(
        self,
        on_change: Callable[[RawDocument], Coroutine],
        on_delete: Callable[[str, str], Coroutine],
        *,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """启动增量监听。
        Args:
            on_change: 文件新增/修改时的回调，参数为 RawDocument。
            on_delete: 文件删除时的回调，参数为 (data_source_id, external_id)。
            loop: 主线程的事件循环，由调用方显式传入。
        """

    @abstractmethod
    def stop_watching(self) -> None:
        """停止监听。"""
