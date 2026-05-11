"""对话并发锁管理"""

from __future__ import annotations

import asyncio

from backend.logging_config import get_logger

logger = get_logger(__name__)

_MAX_HISTORY_LOCKS = 128
_history_locks: dict[str, asyncio.Lock] = {}


class LockCapacityError(Exception):
    """历史锁数量超限"""


class ConversationLock:
    """按 conversation_id 粒度的 asyncio.Lock，带超时和容量限制"""

    def __init__(self, conversation_id: str, *, timeout: float = 30.0):
        self.conversation_id = conversation_id
        self.timeout = timeout
        self._lock: asyncio.Lock | None = None
        self._acquired = False

    async def __aenter__(self):
        if len(_history_locks) >= _MAX_HISTORY_LOCKS:
            _prune_history_locks()
        if len(_history_locks) >= _MAX_HISTORY_LOCKS:
            raise LockCapacityError("历史锁数量已达上限，拒绝请求")
        self._lock = _history_locks.setdefault(self.conversation_id, asyncio.Lock())
        await asyncio.wait_for(self._lock.acquire(), timeout=self.timeout)
        self._acquired = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._acquired and self._lock:
            self._lock.release()


def _prune_history_locks() -> None:
    stale = [cid for cid, lock in _history_locks.items() if not lock.locked()]
    for cid in stale:
        del _history_locks[cid]
