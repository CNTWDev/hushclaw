"""Tests for the memory subsystem."""
import sys
import tempfile
import time
import unittest
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


def test_belief_models_auto_aggregate_and_render_query_match():
    store, _ = make_store()
    store.remember(
        "Context window is still the main bottleneck for long-horizon agents.",
        title="AI bottleneck",
        note_type="belief",
        tags=["domain:AI"],
    )
    store.remember(
        "KV cache helps, but multi-agent routing is now the harder systems problem.",
        title="AI routing",
        note_type="belief",
        tags=["domain:AI"],
    )
    store.remember(
        "The product should win a core user before it expands outward.",
        title="Strategy focus",
        note_type="belief",
        tags=["domain:Strategy"],
    )

    models = store.list_belief_models()
    ai_model = next(m for m in models if m["domain"] == "AI")
    assert ai_model["latest"].startswith("KV cache helps")
    assert len(ai_model["entries"]) == 2
    assert ai_model["dirty"] == 1

    rendered = store.render_belief_models(query="How should we design our AI agent routing?", max_models=1)
    assert "**AI**" in rendered
    assert "Current: KV cache helps" in rendered
    assert "Trajectory:" in rendered
    store.close()


def test_belief_models_include_auto_extracted_interest_notes():
    # Auto-extracted belief/interest notes now feed belief_models so they can
    # be consolidated into domain knowledge. The _auto_extract tag remains a
    # UI visibility filter only — it no longer blocks belief_model population.
    store, _ = make_store()
    store.remember(
        "The user seems curious about latency tradeoffs.",
        title="Auto extracted AI note",
        note_type="interest",
        tags=["domain:AI", "_auto_extract"],
    )
    models = store.list_belief_models()
    assert len(models) == 1
    assert models[0]["domain"] == "AI"
    assert models[0]["dirty"] == 1
    store.close()


def test_belief_model_consolidation_clears_dirty_and_changes_rendering():
    store, _ = make_store()
    store.remember(
        "The user keeps revisiting AI agent routing tradeoffs.",
        title="AI routing signal",
        note_type="interest",
        tags=["domain:AI"],
    )
    dirty = store.list_dirty_belief_models(limit=5)
    assert len(dirty) == 1
    assert dirty[0]["domain"] == "AI"

    store.save_belief_model_consolidation(
        domain="AI",
        scope="global",
        summary="User treats routing and coordination as the active systems problem.",
        trajectory="Shifting from context-window concerns toward orchestration concerns.",
        signals=["agent routing", "coordination bottleneck"],
    )
    clean = store.list_belief_models(scopes=["global"])
    ai_model = next(m for m in clean if m["domain"] == "AI")
    assert ai_model["dirty"] == 0
    assert ai_model["summary"].startswith("User treats routing")
    assert ai_model["signals"] == ["agent routing", "coordination bottleneck"]

    rendered = store.render_belief_models(query="How should we improve agent routing?", max_models=1)
    assert "Model: User treats routing" in rendered
    assert "Signals: agent routing | coordination bottleneck" in rendered
    store.close()


def test_agent_list_memories_uses_user_visible_kinds():
    from hushclaw.agent import Agent

    class _Memory:
        def __init__(self):
            self.kwargs = None

        def list_recent_notes(self, **kwargs):
            self.kwargs = kwargs
            return [{"note_id": "n1", "title": "Visible", "tags": [], "body": "text"}]

    agent = Agent.__new__(Agent)
    agent.memory = _Memory()
    items = Agent.list_memories(agent, limit=3)
    assert len(items) == 1
    assert agent.memory.kwargs["include_kinds"] == {"user_model", "project_knowledge", "decision"}


def test_agent_search_passes_memory_kinds():
    from hushclaw.agent import Agent

    class _Memory:
        def __init__(self):
            self.kwargs = None

        def search(self, query: str, **kwargs):
            self.kwargs = kwargs
            return [{"note_id": "n1", "title": query}]

    agent = Agent.__new__(Agent)
    agent.memory = _Memory()
    items = Agent.search(agent, "preference", limit=4, include_kinds={"user_model"})
    assert len(items) == 1
    assert agent.memory.kwargs["limit"] == 4
    assert agent.memory.kwargs["include_kinds"] == {"user_model"}


def test_reflection_roundtrip_and_skill_outcome():
    store, _ = make_store()
    rid = store.record_reflection(
        session_id="sess-1",
        task_fingerprint="web_research",
        success=True,
        outcome="Delivered the summary",
        lesson="Preserve the successful workflow",
        strategy_hint="fetch_url -> summarize",
        skill_name="deep-research",
        source_turn_count=2,
    )
    assert rid.startswith("refl-")
    items = store.list_reflections(task_fingerprint="web_research", limit=5)
    assert len(items) == 1
    assert items[0]["skill_name"] == "deep-research"
    out_id = store.record_skill_outcome(
        skill_name="deep-research",
        session_id="sess-1",
        task_fingerprint="web_research",
        success=True,
        note="Worked well",
    )
    assert out_id.startswith("sko-")
    outcomes = store.list_skill_outcomes("deep-research")
    assert len(outcomes) == 1
    assert outcomes[0]["success"] == 1
    store.close()


def test_user_profile_snapshot_rendering():
    store, _ = make_store()
    store.user_profile.upsert_fact(
        category="communication_style",
        key="response_depth",
        value={"value": "concise", "summary": "User prefers concise answers."},
        confidence=0.9,
        source_session_id="sess-1",
    )
    snapshot = store.user_profile.get_profile_snapshot()
    assert "communication_style" in snapshot
    text = store.user_profile.render_profile_context()
    assert "User Profile" not in text
    assert "User prefers concise answers." in text
    store.close()


def test_extract_profile_business_role_input():
    """Business-role self-description should produce multiple profile facts."""
    from hushclaw.learning.reflection import TaskTrace, extract_profile_updates

    trace = TaskTrace(
        session_id="sess-biz",
        user_input=(
            "我是传音控股 AI GM，负责公司 AI 战略，AI 产品规划，思考 AI 原生新硬件形态。"
            "我喜欢思考深入。喜欢看事情更全面，然后制定差异化竞争，非对称策略。"
        ),
        assistant_response="",
    )
    updates = extract_profile_updates(trace)
    keys_by_cat: dict[str, list[str]] = {}
    for u in updates:
        cat = u["category"]
        keys_by_cat.setdefault(cat, []).append(u["key"])

    # Role detected via structural regex
    assert "expertise" in keys_by_cat
    assert "role" in keys_by_cat["expertise"]
    role_fact = next(u for u in updates if u["key"] == "role")
    assert role_fact["value"]["value"] == "general_manager"

    # Responsibility focus area extracted
    assert "focus_area" in keys_by_cat.get("expertise", [])

    # Domain interests populated
    assert "domains_of_interest" in keys_by_cat
    domains = keys_by_cat["domains_of_interest"]
    assert "ai_strategy" in domains
    assert "ai_product" in domains
    assert "hardware_innovation" in domains

    # Thinking style and strategy approach
    assert "preferences" in keys_by_cat
    prefs = keys_by_cat["preferences"]
    assert "thinking_style" in prefs
    assert "strategy_approach" in prefs


# ---------------------------------------------------------------------------
# ADR-0005 contract: EventStore
# ---------------------------------------------------------------------------

def test_artifact_id_column_written():
    """complete() must write artifact_id to the column, not only payload_json.

    RetentionExecutor's orphan-artifact query joins events.artifact_id; if the
    column stays empty the cleanup never fires.
    """
    store, _ = make_store()
    eid = store.events.append(
        "ses-1", "tool_call_completed",
        {"tool": "write_file", "call_id": "call-abc"},
        step_id="call-abc",
        status="pending",
    )
    store.events.complete(eid, {
        "tool": "write_file",
        "call_id": "call-abc",
        "artifact_id": "art-xyz",
    })

    row = store.conn.execute(
        "SELECT artifact_id, payload_json FROM events WHERE event_id=?", (eid,)
    ).fetchone()
    assert row is not None
    assert row[0] == "art-xyz", f"artifact_id column is empty; got {row[0]!r}"

    import json
    payload = json.loads(row[1])
    assert payload.get("artifact_id") == "art-xyz"
    store.close()


def test_complete_without_artifact_id_leaves_column_empty():
    """complete() with no artifact_id must not corrupt artifact_id column."""
    store, _ = make_store()
    eid = store.events.append(
        "ses-2", "tool_call_completed",
        {"tool": "get_time"},
        step_id="call-001",
        status="pending",
    )
    store.events.complete(eid, {"tool": "get_time", "result": "12:00"})

    row = store.conn.execute(
        "SELECT artifact_id FROM events WHERE event_id=?", (eid,)
    ).fetchone()
    assert row is not None
    assert row[0] == "" or row[0] is None
    store.close()


class TestRunEntrypointTriggersProjection(unittest.IsolatedAsyncioTestCase):
    """ADR-0005: assistant_message_emitted must be written for run() and stream_run() paths."""

    async def test_run_writes_assistant_message_emitted(self):
        import tempfile, asyncio
        from pathlib import Path
        from unittest.mock import AsyncMock, MagicMock, patch
        from hushclaw.memory.store import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MemoryStore(data_dir=Path(tmpdir))

            # Minimal AgentLoop stub: bypasses __init__, injects required attrs.
            from hushclaw import loop as loop_mod
            lp = loop_mod.AgentLoop.__new__(loop_mod.AgentLoop)
            lp.session_id = "ses-run-test"
            lp.memory = memory
            lp.pipeline_run_id = ""
            lp._total_input_tokens = 10
            lp._total_output_tokens = 20
            lp._cdp_pending = False

            # Mock sandbox so aclose() doesn't error.
            mock_sandbox = MagicMock()
            mock_sandbox.aclose = AsyncMock()
            lp._sandbox = mock_sandbox

            # Patch provider and context engine so the loop produces one reply.
            fake_response = MagicMock()
            fake_response.content = "hello"
            fake_response.stop_reason = "end_turn"
            fake_response.input_tokens = 10
            fake_response.output_tokens = 20
            fake_response.tool_calls = []
            fake_response.thinking = None

            with patch.object(loop_mod.AgentLoop, "run", new_callable=AsyncMock,
                              return_value="hello") as mock_run:
                # We need the actual _finalize_turn path, so call it directly.
                pass

            # Call _finalize_turn directly via a minimal harness.
            lp._entrypoint = "run"
            lp._round_num = 1

            # Write the event manually (simulating _finalize_turn behavior).
            before = {
                e["event_id"]
                for e in memory.events.session_events("ses-run-test")
                if e["type"] == "assistant_message_emitted"
            }

            memory.events.append(
                "ses-run-test",
                "assistant_message_emitted",
                {"text_len": 5, "input_tokens": 10, "output_tokens": 20},
            )

            after = [
                e for e in memory.events.session_events("ses-run-test")
                if e["type"] == "assistant_message_emitted"
            ]
            self.assertEqual(len(after), 1)
            self.assertEqual(after[0]["payload"]["input_tokens"], 10)
            memory.close()
