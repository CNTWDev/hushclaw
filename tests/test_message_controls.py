from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from hushclaw.config.schema import AgentConfig
from hushclaw.context.engine import DefaultContextEngine
from hushclaw.context.policy import ContextPolicy
from hushclaw.memory.store import MemoryStore
from hushclaw.os_api import AgentOSService


def test_message_state_hides_history_but_keeps_event_audit():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td))
        turn_id = mem.save_turn("s-1", "user", "keep this local")
        message_id = f"turn:{turn_id}"

        assert mem.set_message_state(message_id, hidden=True)
        assert mem.load_session_history("s-1") == []

        rows = mem.conn.execute(
            "SELECT type, payload_json FROM events WHERE session_id='s-1' ORDER BY ts"
        ).fetchall()
        assert rows[-1]["type"] == "message_state_changed"
        assert message_id in rows[-1]["payload_json"]


def test_excluded_event_message_is_not_replayed_as_direct_context():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td))
        event_id = mem.events.append(
            "s-1",
            "user_message_received",
            {"input": "do not inject me directly"},
        )
        message_id = f"event:{event_id}"

        assert mem.set_message_state(message_id, excluded=True)
        assert mem.session_log.replay_context(session_id="s-1") == []

        history = mem.load_session_history("s-1")
        assert len(history) == 1
        assert history[0]["message_id"] == message_id
        assert history[0]["excluded"] is True


def test_delete_message_purges_history_reference_and_replay():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td))
        event_id = mem.events.append(
            "s-1",
            "user_message_received",
            {"input": "remove me everywhere"},
        )
        message_id = f"event:{event_id}"

        assert mem.set_message_state(message_id, hidden=True, excluded=True, purged=True)

        assert mem.load_session_history("s-1") == []
        assert mem.session_log.replay_context(session_id="s-1") == []
        assert mem.resolve_message_ref(message_id, session_id="s-1") is None


def test_delete_message_removes_source_linked_derived_data():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td))
        event_id = mem.events.append(
            "s-1",
            "assistant_message_emitted",
            {"text": "done"},
        )
        message_id = f"event:{event_id}"

        note_id = mem.remember(
            "User likes compact answers.",
            title="Auto: compact answers",
            tags=["_auto_extract"],
            note_type="preference",
            persist_to_disk=False,
            source_message_id=message_id,
        )
        fact_id = mem.user_profile.upsert_fact(
            category="communication_style",
            key="response_depth",
            value={"value": "concise"},
            source_session_id="s-1",
            source_message_id=message_id,
        )
        reflection_id = mem.record_reflection(
            session_id="s-1",
            task_fingerprint="general",
            success=True,
            outcome="ok",
            source_message_id=message_id,
        )

        stats = mem.delete_message_derived_data(message_id)

        assert stats == {"notes": 1, "profile_facts": 1, "reflections": 1}
        assert mem.get_note(note_id) is None
        assert not mem.user_profile.delete_fact(fact_id)
        rows = mem.conn.execute(
            "SELECT 1 FROM reflections WHERE reflection_id=?",
            (reflection_id,),
        ).fetchall()
        assert rows == []


def test_agent_os_delete_message_action_soft_deletes_and_clears_cache():
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td))
        event_id = mem.events.append(
            "s-1",
            "user_message_received",
            {"input": "delete through os"},
        )
        message_id = f"event:{event_id}"
        gateway = MagicMock()
        gateway.memory = mem
        os_svc = AgentOSService(gateway)

        result = os_svc.set_message_state(message_id, session_id="s-1", action="delete")

        assert result["ok"] is True
        assert result["action"] == "delete"
        assert result["derived_deleted"] == {"notes": 0, "profile_facts": 0, "reflections": 0}
        assert mem.load_session_history("s-1") == []
        gateway.clear_all_cached_loops.assert_called_once()


def test_context_assembler_injects_referenced_messages_with_budget():
    engine = DefaultContextEngine()
    memory = MagicMock()
    memory.user_profile.render_profile_context = MagicMock(return_value="")
    memory.render_belief_models = MagicMock(return_value="")
    memory.load_session_working_state = MagicMock(return_value="")
    memory.recall_with_budget = MagicMock(return_value="")
    memory.resolve_message_ref = MagicMock(return_value={
        "message_id": "event:abc",
        "role": "assistant",
        "content": "This is the exact idea the user selected.",
        "ts": 123,
    })
    config = AgentConfig(system_prompt="You are HushClaw.", instructions="")
    policy = ContextPolicy(reference_max_tokens=20, reference_max_items=1)

    _stable, dynamic = asyncio.run(engine.assemble(
        "continue",
        policy,
        memory,
        config,
        session_id="s-1",
        references=[{"message_id": "event:abc"}],
    ))

    assert "## Referenced Messages" in dynamic
    assert "[event:abc][assistant][123]" in dynamic
    assert "exact idea" in dynamic
