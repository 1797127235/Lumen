"""工具发现状态 — 按 conversation_id 维护预加载缓存（LRU）。"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class ToolDiscoveryState:
    """管理每个 conversation 的工具可见性缓存。

    设计为无状态重置：进程重启后缓存清空，用户需重新解锁工具。
    这是可接受的，因为 always_on 覆盖了核心能力，且解锁只需一轮 tool_search。

    注意：此单例仅在单进程模式下有效。若使用多 worker（如 gunicorn），
    每个进程有独立缓存，用户在一个 worker 解锁的工具对另一个 worker 不可见。
    长期演进可考虑将缓存持久化到 conversations.metadata_json 字段。
    """

    capacity: int = 10
    max_conversations: int = 1000
    _cache: OrderedDict[str, OrderedDict[str, None]] = field(default_factory=OrderedDict, repr=False)

    def get_visible(self, conversation_id: str | None) -> list[str]:
        """返回当前 conversation 预加载的工具列表（最近使用的在前）。"""
        if conversation_id is None:
            return []
        od = self._cache.get(conversation_id)
        if not od:
            return []
        # OrderedDict 的 keys() 按插入/移动顺序，最新在末尾
        return list(reversed(od.keys()))

    def update(
        self,
        conversation_id: str | None,
        tool_names: list[str],
        always_on: set[str],
    ) -> None:
        """将使用过的工具加入缓存，去重并淘汰溢出项。"""
        if conversation_id is None:
            return

        if conversation_id not in self._cache:
            if len(self._cache) >= self.max_conversations:
                self._cache.popitem(last=False)
            self._cache[conversation_id] = OrderedDict()

        od = self._cache[conversation_id]
        self._cache.move_to_end(conversation_id)

        # 逆序遍历，使得传入列表中靠后的元素在 OrderedDict 中也靠后
        # get_visible() 反转后，最近使用的元素排在最前
        for name in reversed(tool_names):
            if name in always_on:
                continue
            if name in od:
                od.move_to_end(name)
            else:
                od[name] = None

        while len(od) > self.capacity:
            od.popitem(last=False)

    def clear(self, conversation_id: str | None) -> None:
        if conversation_id is not None:
            self._cache.pop(conversation_id, None)


# 模块级单例
_DISCOVERY_STATE: ToolDiscoveryState | None = None


def get_tool_discovery_state() -> ToolDiscoveryState:
    global _DISCOVERY_STATE
    if _DISCOVERY_STATE is None:
        _DISCOVERY_STATE = ToolDiscoveryState()
    return _DISCOVERY_STATE


def reset_tool_discovery_state() -> None:
    global _DISCOVERY_STATE
    _DISCOVERY_STATE = ToolDiscoveryState()
