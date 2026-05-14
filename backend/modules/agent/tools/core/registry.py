"""工具注册表 — 以工具定义为中心，支持 builtin / plugin / MCP 统一注入。"""

from __future__ import annotations

from backend.modules.agent.tools.core.definitions import ToolDefinition


class ToolRegistry:
    """工具注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._toolsets: dict[str, set[str]] = {}

    # -- 注册 --

    def register(self, tool: ToolDefinition) -> None:
        """注册单个工具。"""
        if tool.name in self._tools:
            raise ValueError(f"工具 '{tool.name}' 已注册")
        self._tools[tool.name] = tool

    def register_many(self, tools: list[ToolDefinition]) -> None:
        """批量注册。"""
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> None:
        """注销工具。"""
        self._tools.pop(name, None)
        # 从所有 toolset 中移除
        for tools in self._toolsets.values():
            tools.discard(name)

    # -- 查询 --

    def get(self, name: str) -> ToolDefinition | None:
        """按名称获取工具定义。"""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """检查工具是否已注册。"""
        return name in self._tools

    def list_tools(self) -> list[str]:
        """列出所有已注册的工具名。"""
        return list(self._tools.keys())

    def all_tools(self) -> dict[str, ToolDefinition]:
        """获取所有工具定义的副本。"""
        return dict(self._tools)

    # -- Toolset 管理 --

    def define_toolset(self, name: str, tools: set[str]) -> None:
        """定义一个 toolset（工具名集合）。"""
        self._toolsets[name] = set(tools)

    def get_toolset(self, name: str) -> set[str]:
        """获取 toolset 包含的工具名。"""
        return set(self._toolsets.get(name, set()))

    def list_toolsets(self) -> list[str]:
        """列出所有 toolset 名称。"""
        return list(self._toolsets.keys())

    def resolve_toolset(self, name: str, *, include_nested: bool = True) -> set[str]:
        """解析 toolset，返回包含的所有工具名（支持嵌套 includes）。"""
        # 简单实现：不支持嵌套 includes（Phase 1 先保持简单）
        return self.get_toolset(name)

    def get_tools_for_model(self, toolset_names: list[str] | None = None) -> dict[str, ToolDefinition]:
        """获取暴露给模型的工具定义。

        Args:
            toolset_names: 若提供，只返回这些 toolset 中的工具；
                          若为 None，返回所有已注册工具。
        """
        if toolset_names is None:
            return dict(self._tools)

        allowed: set[str] = set()
        for ts_name in toolset_names:
            allowed |= self.resolve_toolset(ts_name)

        return {name: tool for name, tool in self._tools.items() if name in allowed}

    # -- 内置快捷方法 --

    @classmethod
    def create_default(cls) -> ToolRegistry:
        """创建默认注册表（空）。"""
        return cls()
