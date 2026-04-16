"""Tests for the memory subsystem."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hushclaw.memory.kinds import DECISION, PROJECT_KNOWLEDGE, TELEMETRY, USER_MODEL
from hushclaw.memory.store import MemoryStore


def make_store():
    d = tempfile.mkdtemp()
    return MemoryStore(data_dir=Path(d)), d


def test_remember_and_recall():
    store, _ = make_store()
    nid = store.remember("HushClaw is a Python AI agent framework", title="HushClaw intro")
    assert len(nid) > 0
    note = store.get_note(nid)
    assert note is not None
    assert "HushClaw" in note["body"]
    assert note["memory_kind"] == PROJECT_KNOWLEDGE
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


def test_list_sessions_has_title_and_preview():
    store, _ = make_store()
    sid = "session-readable"
    store.save_turn(sid, "user", "How do we design a resilient payment retry strategy?")
    store.save_turn(sid, "assistant", "Use exponential backoff, jitter, and idempotency keys.")
    sessions = store.list_sessions(limit=10)
    item = next(s for s in sessions if s["session_id"] == sid)
    assert item["title"]
    assert item["last_preview"]
    assert item["kind"] == "chat"
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


def test_search_sessions():
    store, _ = make_store()
    store.save_turn("session-a", "user", "Investigate payment retry strategy")
    store.save_turn("session-b", "user", "Prepare travel checklist")
    results = store.search_sessions("payment retry", limit=10)
    assert len(results) > 0
    assert any(r["session_id"] == "session-a" for r in results)
    store.close()


def test_session_lineage_records_compaction():
    store, _ = make_store()
    sid = "session-compaction"
    store.save_turn(sid, "user", "Tell me what changed in the architecture")
    store.save_session_summary(sid, "Architecture summary")
    lineage_id = store.record_session_compaction(sid, archived=4, kept=2)
    assert lineage_id.startswith("lin-")
    lineage = store.get_session_lineage(sid)
    assert len(lineage) == 1
    assert lineage[0]["relationship"] == "compacted"
    assert lineage[0]["meta_json"]["archived"] == 4
    sessions = store.list_sessions(limit=10)
    item = next(s for s in sessions if s["session_id"] == sid)
    assert item["compaction_count"] == 1
    store.close()


def test_session_working_state_roundtrip():
    store, _ = make_store()
    sid = "session-working-state"
    store.save_session_working_state(sid, "### Active Goal\nShip the session search UI")
    state = store.load_session_working_state(sid)
    assert "Ship the session search UI" in (state or "")
    store.close()


def test_recall_with_budget_zero_means_no_token_cap():
    store, _ = make_store()
    store.remember("A" * 500, title="m1")
    store.remember("B" * 500, title="m2")
    # max_tokens=0 should not stop on token budget.
    out = store.recall_with_budget("", limit=10, min_score=0.0, max_tokens=0)
    assert "[m1]" in out
    assert "[m2]" in out
    # Should include more than one entry for this generous query/limit.
    assert out.count("\n\n") >= 1
    store.close()


def test_memory_kind_inferred_from_note_type():
    store, _ = make_store()
    pref = store.remember("The user prefers concise answers", title="Pref", note_type="preference")
    decision = store.remember("Use the dual-release flow", title="Decision", note_type="decision")
    action = store.remember("Ran the tests", title="Action", note_type="action_log")
    assert store.get_note(pref)["memory_kind"] == USER_MODEL
    assert store.get_note(decision)["memory_kind"] == DECISION
    assert store.get_note(action)["memory_kind"] == TELEMETRY
    store.close()


def test_recall_excludes_telemetry_and_session_memory():
    store, _ = make_store()
    store.remember("The user prefers concise answers", title="Preference", note_type="preference")
    store.remember("Tool ran successfully", title="Telemetry", memory_kind=TELEMETRY, persist_to_disk=False)
    store.remember("Archived session context", title="Archive", memory_kind="session_memory", persist_to_disk=False)
    text = store.recall_with_budget("prefers concise", limit=10, min_score=0.0, max_tokens=0)
    assert "[Preference]" in text
    assert "[Telemetry]" not in text
    assert "[Archive]" not in text
    store.close()
