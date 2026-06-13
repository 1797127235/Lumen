import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.chat.agent_runner import get_history_since_consolidated
from lib.session.manager import SessionManager
from lib.session.store import SessionStore


def _memory_manager() -> SessionManager:
    manager = SessionManager.__new__(SessionManager)
    manager.workspace = Path(":memory:")
    manager.session_dir = Path(":memory:")
    manager.db_path = ":memory:"
    manager._store = SessionStore(":memory:")
    manager._cache = {}
    manager._write_locks = {}
    return manager


def test_prune_for_context_cache_keeps_recent_turn_boundary():
    manager = _memory_manager()
    session = manager.get_or_create("telegram:test")

    for i in range(3):
        session.add_message("user", f"user {i}", llm_context_frame=f"context {i}")
        session.add_message("assistant", f"assistant {i}")
    manager.save(session)

    deleted = manager.prune_for_context_cache(session, keep_messages=2, trigger_messages=5)

    assert deleted == 4
    assert session.last_consolidated == 0
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert [m["content"] for m in session.messages] == ["user 2", "assistant 2"]

    manager.invalidate(session.key)
    reloaded = manager.get_or_create(session.key)
    assert [m["content"] for m in reloaded.messages] == ["user 2", "assistant 2"]


def test_prune_for_context_cache_moves_cutoff_back_to_user_boundary():
    manager = _memory_manager()
    session = manager.get_or_create("telegram:test")

    session.add_message("user", "user 0")
    session.add_message("assistant", "assistant 0")
    session.add_message("user", "user 1")
    session.add_message("assistant", "assistant 1")
    session.add_message("user", "user 2")
    manager.save(session)

    deleted = manager.prune_for_context_cache(session, keep_messages=2, trigger_messages=4)

    assert deleted == 2
    assert [m["content"] for m in session.messages] == ["user 1", "assistant 1", "user 2"]


def test_get_history_since_consolidated_uses_cursor():
    class FakeSession:
        last_consolidated = 2

        def __init__(self):
            self.calls = []

        def get_history(self, max_messages=40, *, start_index=None):
            self.calls.append((max_messages, start_index))
            return [{"role": "user", "content": "from cursor"}]

    session = FakeSession()

    history = get_history_since_consolidated(session, 500)

    assert history == [{"role": "user", "content": "from cursor"}]
    assert session.calls == [(500, 2)]
