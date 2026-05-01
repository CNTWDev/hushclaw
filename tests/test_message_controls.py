from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from hushclaw.config.schema import AgentConfig
from hushclaw.context.engine import DefaultContextEngine
from hushclaw.context.policy import ContextPolicy
from hushclaw.memory.store import MemoryStore


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
