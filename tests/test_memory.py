"""Tests for the memory subsystem."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ghostclaw.memory.store import MemoryStore


def make_store():
    d = tempfile.mkdtemp()
    return MemoryStore(data_dir=Path(d)), d


def test_remember_and_recall():
    store, _ = make_store()
    nid = store.remember("GhostClaw is a Python AI agent framework", title="GhostClaw intro")
    assert len(nid) > 0
    note = store.get_note(nid)
    assert note is not None
    assert "GhostClaw" in note["body"]
    store.close()


def test_fts_search():
    store, _ = make_store()
    store.remember("My project uses Python 3.11", title="Tech stack")
    store.remember("I enjoy hiking in the mountains", title="Hobbies")
    results = store.search("Python programming", limit=5)
    assert len(results) > 0
    assert any("Python" in r["body"] for r in results)
    store.close()


def test_session_persistence():
    store, _ = make_store()
    session = "test-session-001"
    store.save_turn(session, "user", "Hello world")
    store.save_turn(session, "assistant", "Hi there!")
    turns = store.load_session_turns(session)
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[1]["role"] == "assistant"
    store.close()


def test_list_sessions():
    store, _ = make_store()
    store.save_turn("session-a", "user", "Hello")
    store.save_turn("session-b", "user", "World")
    sessions = store.list_sessions()
    ids = [s["session_id"] for s in sessions]
    assert "session-a" in ids
    assert "session-b" in ids
    store.close()


def test_hybrid_search_fallback():
    """Should not crash even with no vector embeddings."""
    store, _ = make_store()
    store.remember("Dragons are mythical creatures", title="Dragons")
    results = store.search("mythical beasts", limit=3)
    # May or may not match but should not raise
    assert isinstance(results, list)
    store.close()


def test_session_summary():
    store, _ = make_store()
    store.save_session_summary("sess-xyz", "User discussed Python and AI agents.")
    summary = store.load_session_summary("sess-xyz")
    assert summary == "User discussed Python and AI agents."
    store.close()
