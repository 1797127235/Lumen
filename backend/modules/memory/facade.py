"""记忆层门面 — LumenMemory 统一入口。

保持 API 签名兼容，内部委托到 writer / searcher / projection 三个职责模块。

双管线架构：
- Profile 事件 → .md 投影 + L0 固定注入（不进搜索索引）
- Narrative 事件 → FTS5 索引 + L2 按需召回（Cognee 保留供 Phase 2 外部数据）
"""

from __future__ import annotations

from backend.modules.memory.projection import ProjectionManager, cancel_background_tasks
from backend.modules.memory.searcher import MemorySearcher
from backend.modules.memory.writer import EventSpec, MemoryWriter


class LumenMemory(MemoryWriter, MemorySearcher, ProjectionManager):
    """记忆层统一门面 — 单例，无状态。

    通过多重继承组合三个职责模块：
    - MemoryWriter:   remember / remember_batch
    - MemorySearcher: recall / build_context / list_events / count_events
    - ProjectionManager: sync_projections / rebuild / delete_event / reset
    """


_memory: LumenMemory | None = None


def get_memory() -> LumenMemory:
    global _memory
    if _memory is None:
        _memory = LumenMemory()
    return _memory


__all__ = ["EventSpec", "LumenMemory", "cancel_background_tasks", "get_memory"]
