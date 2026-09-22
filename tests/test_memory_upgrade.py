"""Cross-entrypoint memory visibility and lifecycle regression tests."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

from hushclaw.memory.ports import SQLiteMemoryPort
from hushclaw.memory.retrieval import memory_revision
from hushclaw.memory.store import MemoryStore
from hushclaw.memory.vectors import _fastembed_model


def test_rejection_is_consistent_across_search_recall_and_random(tmp_path):
    store = MemoryStore(tmp_path)
    try:
        port = SQLiteMemoryPort(store)
        note_id = store.remember("Amber project uses PostgreSQL storage", title="amber storage",
                                 scope="workspace:amber", persist_to_disk=False)
        assert port.search("amber storage", scopes=["workspace:amber"])
        before = store.recall_with_budget("amber storage", min_score=0, session_id="s")
        assert "amber storage" in before
        store.conn.execute("INSERT INTO memory_feedback VALUES(?, 'rejected', 1)",
                           ("note:" + note_id,))
        assert store.search("amber storage", scopes=["workspace:amber"]) == []
        assert port.search("amber storage", scopes=["workspace:amber"]) == []
        assert port.recall("amber storage", scopes=["workspace:amber"]) == ""
        assert store.recall_with_budget("amber storage", min_score=0, session_id="s") == ""
        assert store.recall_with_budget("", min_score=0, session_id="s") == ""
        assert store.personalization.context("amber storage", scopes=["workspace:amber"])["evidence"] == []
    finally:
        store.close()


def test_delete_and_update_invalidate_cached_recall_and_fts(tmp_path):
    store = MemoryStore(tmp_path)
    try:
        note_id = store.remember("The archive uses SQLite storage", title="archive storage",
                                 persist_to_disk=False)
        revision = memory_revision(store.conn)
        assert "SQLite" in store.recall_with_budget("SQLite storage", min_score=0,
                                                      session_id="s", max_tokens=0)
        assert store.update_note(note_id, "The archive uses PostgreSQL storage")
        assert memory_revision(store.conn) > revision
        assert "SQLite" not in store.recall_with_budget("SQLite storage", min_score=0,
                                                          session_id="s", max_tokens=0)
        assert any("PostgreSQL" in item["body"] for item in store.search("PostgreSQL"))
        assert store.delete_note(note_id)
        assert store.recall_with_budget("PostgreSQL", min_score=0, session_id="s") == ""
        assert SQLiteMemoryPort(store).search("PostgreSQL") == []
    finally:
        store.close()


def test_extracted_correction_keeps_history_and_changes_current_answer(tmp_path):
    store = MemoryStore(tmp_path)
    try:
        old = store.remember_extracted("Project amber uses Redis", title="amber cache",
                                       scope="workspace:amber")
        assert store.remember_extracted("Project amber uses Redis", title="amber cache",
                                        scope="workspace:amber") == old
        new = store.remember_extracted("Project amber uses Memcached", title="amber cache",
                                       scope="workspace:amber")
        assert new != old
        assert store.get_note(old)["status"] == "superseded"
        assert store.get_note(new)["supersedes_note_id"] == old
        hits = store.search("amber cache", scopes=["workspace:amber"])
        assert [hit["note_id"] for hit in hits] == [new]
        assert [item["note_id"] for item in store.list_recent_notes()] == [new]
        assert [item["note_id"] for item in store.list_recent_notes_by_scopes(["workspace:amber"])] == [new]
        assert store.search("amber cache", scopes=[]) == []
        assert SQLiteMemoryPort(store).search("amber cache", scopes=["workspace:other"]) == []
    finally:
        store.close()


def test_hidden_source_is_excluded_across_entrypoints(tmp_path):
    store = MemoryStore(tmp_path)
    try:
        event_id = store.session_log.append("s", "user_message_received",
                                            {"input": "I prefer Python for data projects"})
        mid = "event:" + event_id
        store.remember("User prefers Python for data projects", title="Python preference",
                       note_type="preference", source_message_id=mid, persist_to_disk=False)
        assert store.search("Python preference")
        assert store.set_message_state(mid, hidden=True)
        assert store.search("Python preference") == []
        assert SQLiteMemoryPort(store).search("Python preference") == []
        assert store.recall_with_budget("Python preference", min_score=0) == ""
    finally:
        store.close()


def test_belief_render_does_not_resurface_superseded_or_rejected_content(tmp_path):
    store = MemoryStore(tmp_path)
    try:
        old = store.remember_extracted("I prefer Redis for the cache", title="cache choice",
                                       note_type="belief")
        new = store.remember_extracted("I prefer SQLite for the cache", title="cache choice",
                                       note_type="belief")
        text = store.render_belief_models(max_chars=1000)
        assert "SQLite" in text and "Redis" not in text
        store.conn.execute("INSERT INTO memory_feedback VALUES(?, 'rejected', 1)",
                           ("note:" + new,))
        assert store.render_belief_models(max_chars=1000) == ""
        assert store.get_note(old)["status"] == "superseded"
    finally:
        store.close()


def test_deleted_source_cannot_be_read_from_cache_or_read_port(tmp_path):
    store = MemoryStore(tmp_path)
    try:
        mid = store.save_turn("s", "user", "I prefer SQLite for archives")
        store.remember("User prefers SQLite for archives", title="archive preference",
                       note_type="preference", source_message_id="turn:" + mid,
                       persist_to_disk=False)
        assert store.recall_with_budget("archive preference", session_id="s", min_score=0)
        store.conn.execute("DELETE FROM turns WHERE turn_id=?", (mid,))
        assert store.recall_with_budget("archive preference", session_id="s", min_score=0) == ""
        assert SQLiteMemoryPort(store).search("archive preference") == []
    finally:
        store.close()


def test_search_keeps_explicit_tag_exclusions(tmp_path):
    store = MemoryStore(tmp_path)
    try:
        store.remember("Notes about amber storage", title="amber notes",
                       tags=["private"], persist_to_disk=False)
        assert store.search("amber storage")
        assert store.search("amber storage", exclude_tags=["private"]) == []
    finally:
        store.close()


def test_optional_local_neural_embedding_uses_query_and_passage_modes(tmp_path):
    calls = []

    class Model:
        def __init__(self, model_name):
            assert model_name == "BAAI/bge-small-zh-v1.5"

        def passage_embed(self, texts):
            calls.append(("passage", list(texts)))
            yield [1.0, 0.0]

        def query_embed(self, text):
            calls.append(("query", text))
            yield [1.0, 0.0]

    _fastembed_model.cache_clear()
    with patch.dict(sys.modules, {"fastembed": SimpleNamespace(TextEmbedding=Model)}):
        store = MemoryStore(tmp_path, embed_provider="fastembed")
        try:
            store.remember("项目使用 SQLite 存储", title="存储决策", persist_to_disk=False)
            assert store.search("存储策略")
            assert [call[0] for call in calls] == ["passage", "query"]
        finally:
            store.close()
    _fastembed_model.cache_clear()
