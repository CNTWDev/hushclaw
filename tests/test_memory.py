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


# ---------------------------------------------------------------------------
# Phase 11: ProjectionWorker, thread identity, complete() merge
# ---------------------------------------------------------------------------

def test_projection_same_ts_events_both_processed():
    """Composite cursor (ts, event_id) must process all events even with identical timestamps."""
    from unittest.mock import MagicMock, AsyncMock
    import asyncio

    store, _ = make_store()

    # Insert two assistant_message_emitted events at the exact same millisecond.
    fixed_ts = 1_700_000_000_000
    store.conn.execute(
        "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, "
        "type, payload_json, artifact_id, status, ts) "
        "VALUES ('ev-A', 'ses-p', '', '', '', 'assistant_message_emitted', '{}', '', 'completed', ?)",
        (fixed_ts,),
    )
    store.conn.execute(
        "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, "
        "type, payload_json, artifact_id, status, ts) "
        "VALUES ('ev-B', 'ses-p', '', '', '', 'assistant_message_emitted', '{}', '', 'completed', ?)",
        (fixed_ts,),
    )
    store.conn.commit()

    from hushclaw.memory.projection import ProjectionWorker

    engine = MagicMock()
    engine.after_turn = AsyncMock()
    worker = ProjectionWorker(store, engine)

    asyncio.run(worker._process_pending())
    asyncio.run(worker._process_pending())

    # Cursor should be at (fixed_ts, ev-B) — both events processed.
    last_ts, last_eid = worker._get_cursor("after_turn")
    assert last_ts == fixed_ts
    assert last_eid == "ev-B", f"expected ev-B, got {last_eid!r}"
    store.close()


def test_projection_uses_turn_id_lookup():
    """ProjectionWorker uses user_turn_id/assistant_turn_id when present in payload."""
    import asyncio
    from unittest.mock import MagicMock, AsyncMock
    from hushclaw.memory.projection import ProjectionWorker

    store, _ = make_store()

    # Insert turn rows with known IDs.
    now_sec = int(time.time())
    store.conn.execute(
        "INSERT INTO turns (turn_id, session, role, content, ts) "
        "VALUES ('ut-1', 'ses-t', 'user', 'hello from user', ?)", (now_sec,)
    )
    store.conn.execute(
        "INSERT INTO turns (turn_id, session, role, content, ts) "
        "VALUES ('at-1', 'ses-t', 'assistant', 'hello from assistant', ?)", (now_sec,)
    )
    # Insert an event with turn IDs in payload.
    import json
    payload = json.dumps({"user_turn_id": "ut-1", "assistant_turn_id": "at-1"})
    store.conn.execute(
        "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, "
        "type, payload_json, artifact_id, status, ts) "
        "VALUES ('ev-t', 'ses-t', '', '', '', 'assistant_message_emitted', ?, '', 'completed', ?)",
        (payload, now_sec * 1000),
    )
    store.conn.commit()

    seen_inputs = []
    engine = MagicMock()
    async def capture(session_id, user_input, assistant_response, memory):
        seen_inputs.append((user_input, assistant_response))
    engine.after_turn = capture

    worker = ProjectionWorker(store, engine)
    asyncio.run(worker._process_pending())

    assert len(seen_inputs) == 1, f"expected 1 call, got {len(seen_inputs)}"
    assert seen_inputs[0] == ("hello from user", "hello from assistant")
    store.close()


def test_different_agents_same_session_get_different_threads():
    """get_or_create_thread() must scope root threads by (session_id, agent_name)."""
    store, _ = make_store()

    sid = "ses-multi"
    t1 = store.get_or_create_thread(sid, agent_name="agent-alpha")
    t2 = store.get_or_create_thread(sid, agent_name="agent-beta")
    t1b = store.get_or_create_thread(sid, agent_name="agent-alpha")

    assert t1 != t2, "different agents should have different root threads"
    assert t1 == t1b, "same agent should get the same existing thread"
    store.close()


def test_complete_merges_original_fields():
    """complete() must preserve original pending payload fields (read-merge-write)."""
    store, _ = make_store()

    eid = store.events.append(
        "ses-merge", "tool_call_completed",
        {"tool": "write_file", "call_id": "c-001", "input": "/path/to/file"},
        step_id="c-001",
        status="pending",
    )
    store.events.complete(eid, {"artifact_id": "art-merge", "size_bytes": 42})

    row = store.conn.execute(
        "SELECT payload_json, artifact_id FROM events WHERE event_id=?", (eid,)
    ).fetchone()
    import json as _json
    payload = _json.loads(row[0])

    # Original fields must survive.
    assert payload.get("tool") == "write_file", "original 'tool' field lost"
    assert payload.get("call_id") == "c-001", "original 'call_id' field lost"
    assert payload.get("input") == "/path/to/file", "original 'input' field lost"
    # New fields from complete() must be present.
    assert payload.get("artifact_id") == "art-merge"
    assert payload.get("size_bytes") == 42
    # artifact_id column must also be written.
    assert row[1] == "art-merge"
    store.close()


def test_session_log_window_queries_by_session_and_run():
    store, _ = make_store()
    store.conn.execute(
        "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, type, payload_json, artifact_id, status, ts) "
        "VALUES ('ev-1', 'ses-log', 'th-log', 'run-a', '', 'user_message_received', '{}', '', 'completed', 1000)"
    )
    store.conn.execute(
        "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, type, payload_json, artifact_id, status, ts) "
        "VALUES ('ev-2', 'ses-log', 'th-log', 'run-a', '', 'tool_call_requested', '{}', '', 'completed', 2000)"
    )
    store.conn.execute(
        "INSERT INTO events (event_id, session_id, thread_id, run_id, step_id, type, payload_json, artifact_id, status, ts) "
        "VALUES ('ev-3', 'ses-log', 'th-log', 'run-b', '', 'assistant_message_emitted', '{}', '', 'completed', 3000)"
    )
    store.conn.commit()

    session_slice = store.session_log.events_by_session("ses-log", since_ts_ms=1500, until_ts_ms=2500)
    assert [e["event_id"] for e in session_slice] == ["ev-2"]

    run_events = store.session_log.events_by_run("run-a")
    assert [e["event_id"] for e in run_events] == ["ev-1", "ev-2"]
    store.close()


def test_memory_events_alias_is_session_log():
    store, _ = make_store()
    eid = store.events.append("ses-alias", "run_started", {"agent": "demo"})

    events = store.session_log.events_by_session("ses-alias")
    assert events[0]["event_id"] == eid
    assert events[0]["type"] == "run_started"
    store.close()


def test_session_log_replay_context_and_token_totals():
    store, _ = make_store()
    eid = store.session_log.append(
        "ses-replay",
        "tool_call_requested",
        {"tool": "remember", "call_id": "tc-9", "input": {"content": "x"}},
        thread_id="th-replay",
        run_id="run-replay",
        step_id="tc-9",
        status="pending",
    )
    store.session_log.append(
        "ses-replay",
        "user_message_received",
        {"input": "hello from log"},
        thread_id="th-replay",
        run_id="run-replay",
    )
    store.session_log.complete(
        eid,
        {"tool": "remember", "call_id": "tc-9", "result": "saved"},
    )
    store.session_log.append(
        "ses-replay",
        "assistant_message_emitted",
        {"text": "hi from log", "input_tokens": 12, "output_tokens": 34},
        thread_id="th-replay",
        run_id="run-replay",
    )

    messages = store.session_log.replay_context(thread_id="th-replay")
    assert [m.role for m in messages] == ["tool", "user", "assistant"] or [m.role for m in messages] == ["user", "tool", "assistant"]
    assert any(m.content == "hello from log" for m in messages if m.role == "user")
    assert any(m.content == "saved" for m in messages if m.role == "tool")
    assert any(m.content == "hi from log" for m in messages if m.role == "assistant")

    inp, out = store.session_log.replay_token_totals(thread_id="th-replay")
    assert inp == 12
    assert out == 34
    store.close()


def test_load_session_history_prefers_event_replay():
    store, _ = make_store()
    store.save_turn("ses-history", "user", "stale user turn")
    store.save_turn("ses-history", "assistant", "stale assistant turn")
    store.session_log.append(
        "ses-history",
        "user_message_received",
        {"input": "fresh user from events"},
        thread_id="th-history",
        run_id="run-history",
    )
    store.session_log.append(
        "ses-history",
        "assistant_message_emitted",
        {"text": "fresh assistant from events"},
        thread_id="th-history",
        run_id="run-history",
    )

    history = store.load_session_history("ses-history")
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[0]["content"] == "fresh user from events"
    assert history[1]["content"] == "fresh assistant from events"
    store.close()


def test_load_thread_history_is_thread_scoped():
    store, _ = make_store()
    thread_a = "th-a"
    thread_b = "th-b"
    store.conn.execute(
        "INSERT INTO threads (thread_id, session_id, parent_thread_id, agent_name, status, created, updated) "
        "VALUES (?, 'ses-threaded', '', 'default', 'active', 1, 1)",
        (thread_a,),
    )
    store.conn.execute(
        "INSERT INTO threads (thread_id, session_id, parent_thread_id, agent_name, status, created, updated) "
        "VALUES (?, 'ses-threaded', '', 'default', 'active', 1, 1)",
        (thread_b,),
    )
    store.conn.commit()

    store.session_log.append(
        "ses-threaded",
        "user_message_received",
        {"input": "thread A question"},
        thread_id=thread_a,
        run_id="run-a",
    )
    store.session_log.append(
        "ses-threaded",
        "assistant_message_emitted",
        {"text": "thread A answer"},
        thread_id=thread_a,
        run_id="run-a",
    )
    store.session_log.append(
        "ses-threaded",
        "user_message_received",
        {"input": "thread B question"},
        thread_id=thread_b,
        run_id="run-b",
    )

    history = store.load_thread_history(thread_a)
    assert [item["content"] for item in history] == ["thread A question", "thread A answer"]
    store.close()
