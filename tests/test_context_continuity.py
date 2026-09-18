"""Regression coverage for cold restore, compaction coverage and corrections."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from hushclaw.context.compactor import CompactionService
from hushclaw.context.policy import ContextPolicy
from hushclaw.memory.context_history import raw_history
from hushclaw.providers.base import LLMResponse, Message
from hushclaw.runtime.harness import HarnessFactory
from tests.test_harness import _make_agent


@pytest.fixture
def agent(tmp_path):
    result = _make_agent(tmp_path)
    yield result
    result.memory.close()


def add_pair(memory, sid, tid, number):
    user = memory.session_log.append(sid, "user_message_received", {"input": f"user-{number}"}, thread_id=tid)
    memory.session_log.append(sid, "assistant_message_emitted", {"text": f"answer-{number}"}, thread_id=tid)
    return "event:" + user


@pytest.mark.parametrize("path", ["session", "thread", "harness"])
def test_legacy_summary_never_discards_latest_correction(agent, path):
    memory = agent.memory
    tid = memory.get_or_create_thread("session", agent_name="test-agent")
    add_pair(memory, "session", tid, 1)
    memory.save_session_summary("session", "old position")
    add_pair(memory, "session", tid, 2)
    if path == "harness":
        loop = HarnessFactory.rebuild_from_events(tid, agent)
    else:
        loop = agent.new_loop("session", **({"thread_id": tid} if path == "thread" else {}))
    assert [m.content for m in loop._context] == ["user-1", "answer-1", "user-2", "answer-2"]


def test_checkpoint_restores_exact_suffix_including_later_turns(agent):
    memory = agent.memory
    tid = memory.get_or_create_thread("s", agent_name="test-agent")
    add_pair(memory, "s", tid, 1)
    boundary = add_pair(memory, "s", tid, 2)
    checkpoint = memory.prepare_context_checkpoint("s", boundary)
    assert memory.save_context_checkpoint(checkpoint, "summary of turn one")
    add_pair(memory, "s", tid, 3)
    restored = HarnessFactory.rebuild_from_events(tid, agent)._context
    assert restored[0].context_kind == "summary"
    assert [m.content for m in restored[1:]] == ["user-2", "answer-2", "user-3", "answer-3"]
    assert agent.new_loop("s")._context == restored
    assert len(raw_history(memory, "s", tid)) == 6  # raw evidence never deleted


@pytest.mark.parametrize("change", ["prefix", "boundary", "exclude"])
def test_stale_checkpoint_falls_back_to_raw_history(agent, change):
    memory = agent.memory
    tid = memory.get_or_create_thread("s", agent_name="test-agent")
    first = add_pair(memory, "s", tid, 1)
    boundary = add_pair(memory, "s", tid, 2)
    cp = memory.prepare_context_checkpoint("s", boundary)
    assert memory.save_context_checkpoint(cp, "obsolete")
    if change == "exclude":
        memory.conn.execute("INSERT INTO message_states(message_id,session_id,excluded,updated) VALUES(?,'s',1,0)", (first,))
    elif change == "boundary":
        memory.conn.execute("DELETE FROM events WHERE event_id=?", (boundary[6:],))
    else:
        memory.conn.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps({"input": "corrected"}), first[6:]))
    memory.conn.commit()
    assert not any(m.context_kind == "summary" for m in memory.restore_context("s", tid))
    assert not memory.save_context_checkpoint(cp, "stale result")


def test_child_thread_never_inherits_parent_summary_or_legacy_turns(agent):
    memory = agent.memory
    root = memory.get_or_create_thread("s", agent_name="test-agent")
    child = memory.get_or_create_thread("s", agent_name="child", parent_thread_id=root)
    memory.save_turn("s", "user", "parent private history")
    memory.save_session_summary("s", "parent summary")
    assert memory.restore_context("s", child) == []
    add_pair(memory, "s", child, "child")
    assert [m.content for m in memory.restore_context("s", child)] == ["user-child", "answer-child"]


def test_telemetry_does_not_consume_replay_limit(agent):
    memory = agent.memory
    tid = memory.get_or_create_thread("s", agent_name="test-agent")
    add_pair(memory, "s", tid, 1)
    memory.conn.executemany(
        "INSERT INTO events(event_id,session_id,thread_id,type,payload_json,status,ts) VALUES(?,?,?,'ws:chunk','{}','completed',0)",
        [(f"noise-{n}", "s", tid) for n in range(10010)],
    )
    memory.conn.commit()
    add_pair(memory, "s", tid, 2)
    assert memory.session_log.replay_context(thread_id=tid)[-1].content == "answer-2"
    assert len(memory.session_log.replay_context(thread_id=tid, limit=4)) == 4


def test_mixed_legacy_history_and_failed_event_write_are_preserved(agent):
    memory = agent.memory
    tid = memory.get_or_create_thread("s", agent_name="test-agent")
    memory.save_turn("s", "user", "legacy viewpoint")
    memory.save_turn("s", "assistant", "legacy feedback")
    user_id = memory.save_turn("s", "user", "new correction")
    memory.session_log.append("s", "user_message_received", {"input": "new correction", "user_turn_id": user_id}, thread_id=tid)
    memory.save_turn("s", "assistant", "answer without event")
    next_id = memory.save_turn("s", "user", "follow up")
    memory.session_log.append("s", "user_message_received", {"input": "follow up", "user_turn_id": next_id}, thread_id=tid)
    assert [m.content for m in memory.restore_context("s", tid)] == [
        "legacy viewpoint", "legacy feedback", "new correction", "answer without event", "follow up",
    ]


def test_excluding_all_events_does_not_resurrect_turn_mirrors(agent):
    memory = agent.memory
    turn = memory.save_turn("s", "user", "excluded text")
    event = memory.session_log.append("s", "user_message_received", {"input": "excluded text", "user_turn_id": turn})
    memory.conn.execute("INSERT INTO message_states(message_id,session_id,excluded,updated) VALUES(?,'s',1,0)", ("event:" + event,))
    assert memory.restore_context("s") == []


def provider_memory(response=None):
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=response or LLMResponse(content="summary", stop_reason="end_turn"))
    memory = MagicMock()
    memory.load_session_working_state.return_value = None
    return provider, memory


def history(rounds=6, size=700):
    return [Message(role=role, content=f"{role}-{n}:" + "x" * size)
            for n in range(rounds) for role in ("user", "assistant")]


def test_all_removed_rounds_are_present_in_summary_input():
    provider, memory = provider_memory()
    messages = history()
    result = asyncio.run(CompactionService().compact(messages, ContextPolicy(history_budget=260, compact_threshold=.5, compact_keep_turns=4), provider, "model", memory, "s"))
    assert result[-2:] == messages[-2:]
    prompt = provider.complete.call_args.kwargs["messages"][0].content
    for message in messages[:-2]:
        assert message.content in prompt


@pytest.mark.parametrize("failure", ["exception", "empty", "truncated", "cancelled"])
def test_failed_summary_keeps_every_message_and_does_not_checkpoint(failure):
    provider, memory = provider_memory()
    if failure == "exception":
        provider.complete.side_effect = RuntimeError("offline")
    elif failure == "cancelled":
        provider.complete.side_effect = asyncio.CancelledError()
    else:
        provider.complete.return_value = LLMResponse(content="" if failure == "empty" else "partial", stop_reason="end_turn" if failure == "empty" else "max_tokens")
    messages = history()
    call = CompactionService().compact(messages, ContextPolicy(compact_keep_turns=2), provider, "model", memory, "s")
    if failure == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(call)
    else:
        assert asyncio.run(call) is messages
    memory.save_context_checkpoint.assert_not_called()
    memory.save_session_summary.assert_not_called()


def test_large_history_is_not_truncated_and_structured_content_is_preserved():
    provider, memory = provider_memory()
    messages = history(4, 70000)
    messages[1].content = [{"type": "text", "text": "critical-list-content"}]
    asyncio.run(CompactionService().compact(messages, ContextPolicy(history_budget=0, compact_keep_turns=1), provider, "model", memory, "s"))
    prompts = "".join(c.kwargs["messages"][0].content for c in provider.complete.call_args_list)
    assert provider.complete.call_count >= 3
    assert "critical-list-content" in prompts
    assert "assistant-2:" in prompts
    assert prompts.count("x") >= sum(m.content.count("x") for m in messages[:-2] if isinstance(m.content, str))


def test_compaction_checkpoint_survives_rebuild_and_second_compaction(agent):
    memory = agent.memory
    tid = memory.get_or_create_thread("s", agent_name="test-agent")
    for n in range(6):
        add_pair(memory, "s", tid, n)
    provider, _ = provider_memory()
    policy = ContextPolicy(compact_keep_turns=2)
    first = asyncio.run(CompactionService().compact(memory.restore_context("s", tid), policy, provider, "model", memory, "s"))
    assert first == memory.restore_context("s", tid)
    for n in range(6, 9):
        add_pair(memory, "s", tid, n)
    second = asyncio.run(CompactionService().compact(memory.restore_context("s", tid), policy, provider, "model", memory, "s"))
    assert second == memory.restore_context("s", tid)
    assert second[-1].content == "answer-8"
    assert len(raw_history(memory, "s", tid)) == 18


def test_pruning_preserves_tool_identifiers():
    tool = Message(role="tool", content="large output", tool_call_id="call", tool_name="read_file", source_id="event:tool")
    messages = [Message(role="user", content="old"), tool, Message(role="user", content="new")]
    result = CompactionService.prune_tool_results(messages, ContextPolicy(compact_keep_turns=1))
    assert result[1].tool_call_id == "call" and result[1].tool_name == "read_file"
    assert result[1].source_id == "event:tool"


def test_replayed_tool_pairs_survive_sanitization_but_not_excluded_runs(agent):
    memory = agent.memory
    tid = memory.get_or_create_thread("s", agent_name="test-agent")
    user = memory.session_log.append("s", "user_message_received", {"input": "read"}, thread_id=tid, run_id="run")
    memory.session_log.append("s", "tool_call_requested", {"tool": "read_file", "call_id": "call", "input": {"path": "a"}, "result": "evidence"}, thread_id=tid, run_id="run")
    loop = agent.new_loop("s", thread_id=tid)
    loop._sanitize_context()
    assert [m.role for m in loop._context] == ["user", "assistant", "tool"]
    memory.conn.execute("INSERT INTO message_states(message_id,session_id,excluded,updated) VALUES(?,'s',1,0)", ("event:" + user,))
    assert memory.restore_context("s", tid) == []


def test_working_state_cache_keeps_global_and_local_context(agent):
    from hushclaw.context.assembler import ContextAssembler
    memory = agent.memory
    memory.save_global_working_state("global goal")
    memory.save_session_working_state("s", "session goal")
    assembler = ContextAssembler(read_file_cached=MagicMock(), resolve_effective_timezone=MagicMock(), build_relative_day_anchors=MagicMock())
    first = assembler._load_working_state(memory, "s")
    assert assembler._load_working_state(memory, "s") == first
    memory.save_global_working_state("updated goal")
    assert "updated goal" in assembler._load_working_state(memory, "s")
