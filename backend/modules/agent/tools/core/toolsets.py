"""Toolset 解析器 — 纯配置层，支持 includes 和动态收窄。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolsetConfig:
    """Toolset 配置定义。"""

    description: str = ""
    """Toolset 描述。"""

    tools: list[str] = field(default_factory=list)
    """直接包含的工具名列表。"""

    includes: list[str] = field(default_factory=list)
    """包含的其他 toolset 名称。"""


class ToolsetResolver:
    """Toolset 解析器。

    支持：
    - 声明式 toolset 定义
    - includes 嵌套引用
    - 按平台 / session 动态收窄
    """

    def __init__(self, toolsets: dict[str, ToolsetConfig] | None = None) -> None:
        # 初始为空，toolsets 通过 register() 在工厂中统一注册，
        # 避免 DEFAULT_TOOLSETS 与 factory.py 中的注册产生双份真相。
        self._toolsets: dict[str, ToolsetConfig] = dict(toolsets or {})

    def register(self, name: str, config: ToolsetConfig) -> None:
        """注册一个 toolset。"""
        self._toolsets[name] = config

    def resolve(self, name: str, *, _visited: set[str] | None = None) -> set[str]:
        """解析 toolset，展开 includes，返回包含的所有工具名。

        Args:
            name: toolset 名称
            _visited: 内部使用，防止循环引用

        Returns:
            工具名集合
        """
        visited = _visited or set()
        if name in visited:
            return set()  # 循环引用，忽略
        visited.add(name)

        config = self._toolsets.get(name)
        if config is None:
            return set()

        tools: set[str] = set(config.tools)
        for include in config.includes:
            tools |= self.resolve(include, _visited=visited)

        return tools

    def resolve_many(self, names: list[str]) -> set[str]:
        """解析多个 toolset，返回并集。"""
        tools: set[str] = set()
        for name in names:
            tools |= self.resolve(name)
        return tools

    def list_toolsets(self) -> list[str]:
        """列出所有 toolset 名称。"""
        return list(self._toolsets.keys())
