"""Lumen 记忆层 — Hermes-Pure 文件优先架构。

导出：
  MemoryProvider, NoOpMemoryProvider, BuiltinMemoryProvider
  MemoryManager
  AsyncMarkdownStore
  discover_providers, load_provider
  get_memory_manager
"""

from __future__ import annotations

from lib.memory.builtin_provider import BuiltinMemoryProvider
from lib.memory.loader import discover_providers, load_provider
from lib.memory.manager import MemoryManager
from lib.memory.markdown import AsyncMarkdownStore
from lib.memory.provider import MemoryProvider, NoOpMemoryProvider

# ── 全局 MemoryManager 单例 ──
_memory_manager: MemoryManager | None = None


def get_memory_manager() -> MemoryManager:
    """获取全局 MemoryManager 单例。"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager


__all__ = [
    "MemoryProvider",
    "NoOpMemoryProvider",
    "BuiltinMemoryProvider",
    "MemoryManager",
    "AsyncMarkdownStore",
    "discover_providers",
    "load_provider",
    "get_memory_manager",
]
