"""Visible stages must arrive before awaited work, not only on completion."""
import json
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace

from hushclaw.providers.base import LLMResponse, Message, ToolCall
from hushclaw.server.chat_mixin import ChatMixin
from hushclaw.server.session import _SessionEntry
from tests import test_streaming as _streaming


def make_loop(**kwargs):
    return _streaming.TestAgentLoopEventStream()._make_loop(**kwargs)


class TestLoopProgress(unittest.IsolatedAsyncioTestCase):
    async def test_preparing_is_delivered_before_context_assembly(self):
        loop = make_loop()
        loop._prepare_turn = AsyncMock(wraps=loop._prepare_turn)
        stream = loop.event_stream("hello")
        first = await anext(stream)
        self.assertEqual(first["phase"], "preparing")
        self.assertGreater(first["started_at"], 0)
        loop._prepare_turn.assert_not_awaited()
        await stream.aclose()

    async def test_compaction_and_first_model_wait_are_published_before_work(self):
        loop = make_loop()
        loop.config.context.history_budget = 500
        loop._context = [Message(role="user", content="x" * 10000)]
        events = []
        async def compact(*args, **kwargs):
            self.assertEqual(events[-1]["phase"], "compacting")
            loop._context = loop._context[-1:]
            return {"effective": True}
        async def complete(**kwargs):
            self.assertEqual(events[-1]["phase"], "waiting_model")
            self.assertEqual(events[-1]["meta"]["round"], 0)
            return LLMResponse(content="done", stop_reason="end_turn")
        loop._maybe_compact_context = AsyncMock(side_effect=compact)
        loop.provider.complete = AsyncMock(side_effect=complete)
        async for event in loop.event_stream("hello"):
            events.append(event)
        self.assertEqual([e["phase"] for e in events if e["type"] == "progress"],
                         ["preparing", "compacting", "waiting_model"])
        self.assertEqual(events[-1]["type"], "done")

    async def test_tools_return_to_model_wait_on_each_round(self):
        loop = make_loop(tool_calls=[ToolCall(id="read", name="remember", input={"content": "fact"})])
        events = [event async for event in loop.event_stream("check")]
        waits = [e for e in events if e.get("phase") == "waiting_model"]
        self.assertEqual([e["meta"]["round"] for e in waits], [0, 1])
        result = next(i for i, e in enumerate(events) if e["type"] == "tool_result")
        next_wait = next(i for i, e in enumerate(events) if e.get("phase") == "waiting_model" and e["meta"]["round"] == 1)
        self.assertLess(result, next_wait)

    async def test_failed_stream_emits_retry_before_fallback_request(self):
        loop = make_loop()
        async def failed_stream(**kwargs):
            raise RuntimeError("test stream unavailable")
            yield
        loop.provider.stream_complete = failed_stream
        events = []
        async def fallback(**kwargs):
            self.assertEqual(events[-1]["phase"], "retrying_model")
            return LLMResponse(content="recovered", stop_reason="end_turn")
        loop.provider.complete = AsyncMock(side_effect=fallback)
        async for event in loop.event_stream("hello"):
            events.append(event)
        self.assertEqual(events[-1]["text"], "recovered")


class TestProgressWire(unittest.IsolatedAsyncioTestCase):
    async def test_kernel_to_chat_to_replay_reports_work_before_awaiting_it(self):
        from hushclaw.server_impl import HushClawServer
        from hushclaw.server.session import _SessionSink
        server = HushClawServer.__new__(HushClawServer)
        server._running_sessions = set()
        server._session_runtime = {}
        server._pending_skill_prompts = {}
        entry = _SessionEntry(session_id="s-progress")
        server._session_tasks = {"s-progress": entry}
        subscriber = AsyncMock()
        entry.subscriber = subscriber
        sink = _SessionSink(entry)
        loop = make_loop()
        loop.config.context.history_budget = 500
        loop._context = [Message(role="user", content="x" * 10000)]

        def last_phase():
            events = [json.loads(c.args[0]) for c in subscriber.send.call_args_list]
            return [e["runtime"]["phase"] for e in events if e["type"] == "session_runtime"][-1]

        async def compact(*args, **kwargs):
            self.assertEqual(last_phase(), "compacting")
            loop._context = loop._context[-1:]
            return {"effective": True}

        async def complete(**kwargs):
            self.assertEqual(last_phase(), "waiting_model")
            return LLMResponse(content="done", stop_reason="end_turn")

        async def events(*args, **kwargs):
            yield {"type": "thread_run_bound", "thread_id": "thread", "run_id": "run"}
            async for event in loop.event_stream("hello"):
                yield event

        loop._maybe_compact_context = AsyncMock(side_effect=compact)
        loop.provider.complete = AsyncMock(side_effect=complete)
        server._gateway = SimpleNamespace(event_stream=events, base_agent=SimpleNamespace(
            config=SimpleNamespace(workspaces=SimpleNamespace(list=[]))))
        await server._handle_chat(sink, {"text": "hello", "session_id": "s-progress"})
        replay = [json.loads(raw) for raw in entry.buffer]
        phases = [e["runtime"]["phase"] for e in replay if e["type"] == "session_runtime"]
        self.assertIn("compacting", phases)
        self.assertIn("waiting_model", phases)
        self.assertLess(phases.index("compacting"), phases.index("waiting_model"))
        self.assertEqual(server._session_runtime["s-progress"]["status"], "completed")

    async def test_snapshot_preserves_stage_time_for_reconnect(self):
        server = ChatMixin()
        server._running_sessions = set()
        entry = _SessionEntry(session_id="s")
        server._session_tasks = {"s": entry}
        entry.begin_run({"text": "hello"}, run_id="run")
        ws = AsyncMock()
        for phase in ("preparing", "compacting", "waiting_model", "retrying_model"):
            await server._emit_agent_progress(ws, "s", {
                "phase": phase, "started_at": 1234000, "meta": {"round": 0},
            }, entry=entry, run_id="run", agent="default")
            snapshots = [json.loads(call.args[0]) for call in ws.send.call_args_list]
            current = [e["runtime"] for e in snapshots if e["type"] == "session_runtime"][-1]
            self.assertEqual(current["phase"], phase)
            self.assertEqual(current["phase_started_at"], 1234000)
            self.assertEqual(server._session_runtime["s"], current)
            self.assertEqual(current["active_step"]["meta"]["phase"], phase)
            await server._emit_session_phase(ws, "s", phase, current["summary"])
            self.assertEqual(server._session_runtime["s"]["phase_started_at"], 1234000)

    async def test_unknown_progress_cannot_render_arbitrary_internal_text(self):
        server = ChatMixin()
        ws = AsyncMock()
        await server._emit_agent_progress(ws, "s", {"phase": "raw_thought", "summary": "private"}, entry=None, run_id="", agent="default")
        ws.send.assert_not_awaited()
