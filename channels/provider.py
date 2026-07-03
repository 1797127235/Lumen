"""Channel Provider 抽象基类。

每个 ChannelProvider 是一个工厂，负责根据配置构造 BaseChannel 实例。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from lib.bus.event_bus import EventBus
from lib.bus.queue import MessageBus

from .base import BaseChannel


class ChannelProvider(ABC):
    """Channel 插件抽象基类。"""

    def __init__(self, **kwargs: Any) -> None:
        """Provider 构造函数通常不需要参数；接受 kwargs 以兼容通用加载器。"""
        _ = kwargs

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel provider 类型名，如 web / telegram / discord。"""
        ...

    @abstractmethod
    def build(
        self,
        config: dict[str, Any],
        *,
        bus: MessageBus,
        event_bus: EventBus,
    ) -> BaseChannel:
        """根据配置构造 BaseChannel 实例。"""
        ...

    def get_config_schema(self) -> list[dict]:
        """返回该 channel 的配置项 schema（用于设置 UI）。"""
        return []
