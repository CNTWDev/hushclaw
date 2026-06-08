"""Tests for SSE streaming in AnthropicRawProvider and AgentLoop.event_stream."""
from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from hushclaw.providers.base import StreamEvent


class TestStreamEvent(unittest.TestCase):
    def test_defaults(self):
        ev = StreamEvent(type="text")
        self.assertEqual(ev.text, "")
        self.assertEqual(ev.tool_name, "")
        self.assertEqual(ev.tool_input, {})
        self.assertEqual(ev.input_tokens, 0)

    def test_fields(self):
        ev = StreamEvent(
            type="done",
            text="hello",
            input_tokens=10,
            output_tokens=5,
        )
        self.assertEqual(ev.type, "done")
        self.assertEqual(ev.text, "hello")
        self.assertEqual(ev.input_tokens, 10)

    def test_awaiting_user_is_replayable_wire_event(self):
        from hushclaw.server.session import _REPLAY_EVENTS

        self.assertIn("awaiting_user", _REPLAY_EVENTS)
        self.assertIn("session_runtime", _REPLAY_EVENTS)


class TestAsyncRuntimePlumbing(unittest.IsolatedAsyncioTestCase):
    async def test_background_hook_does_not_block_emit(self):
        from hushclaw.runtime.hooks import HookBus

        bus = HookBus()
        ran = asyncio.Event()

        async def _slow(_event):
            await asyncio.sleep(0.05)
            ran.set()

        bus.on("post_turn_persist", _slow, background=True)
        started = asyncio.get_running_loop().time()
        await bus.emit("post_turn_persist", session_id="s-1")
        elapsed = asyncio.get_running_loop().time() - started

        self.assertLess(elapsed, 0.04)
        self.assertFalse(ran.is_set())
        await asyncio.wait_for(ran.wait(), timeout=1)

    async def test_session_sink_flushes_wire_events_in_background(self):
        from hushclaw.server.session import _SessionEntry, _SessionSink

        class _Conn:
            def __init__(self):
                self.rows = []
                self.commits = 0

            def executemany(self, _sql, rows):
                self.rows.extend(rows)

            def commit(self):
                self.commits += 1

        class _Subscriber:
            def __init__(self):
                self.sent = []

            async def send(self, raw):
                self.sent.append(raw)

        conn = _Conn()
        sub = _Subscriber()
        entry = _SessionEntry(
            session_id="s-1",
            memory=SimpleNamespace(conn=conn),
            subscriber=sub,
        )
        sink = _SessionSink(entry)

        await sink.send(json.dumps({"type": "done", "text": "ok"}))

        self.assertEqual(len(sub.sent), 1)
        self.assertEqual(conn.rows, [])
        self.assertEqual(len(entry.pending_wire_events), 1)

        await asyncio.sleep(0.08)

        self.assertEqual(len(conn.rows), 1)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(entry.pending_wire_events, [])


class TestAnthropicRawSSE(unittest.TestCase):
    """Test _sync_sse_stream SSE line parsing logic."""

    def _make_provider(self):
        from hushclaw.providers.anthropic_raw import AnthropicRawProvider
        with patch.object(AnthropicRawProvider, "__init__", lambda self, **kw: None):
            p = AnthropicRawProvider.__new__(AnthropicRawProvider)
            p.api_key = "test-key"
            p.base_url = "https://api.anthropic.com/v1"
            p.timeout = 30
        return p

    def _make_sse_lines(self, events: list[dict]) -> list[bytes]:
        lines = []
        for ev in events:
            lines.append(f"data: {json.dumps(ev)}\n".encode())
        return lines

    def test_text_delta_extracted(self):
        p = self._make_provider()
        sse_events = [
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": ", world!"}},
            {"type": "message_stop"},
        ]
        lines = self._make_sse_lines(sse_events)

        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.__iter__ = MagicMock(return_value=iter(lines))
            mock_open.return_value = mock_resp

            chunks = list(p._sync_sse_stream({"model": "claude-sonnet-4-6", "messages": []}))

        self.assertEqual(chunks, ["Hello", ", world!"])

    def test_non_text_delta_ignored(self):
        p = self._make_provider()
        sse_events = [
            {"type": "content_block_start", "content_block": {"type": "tool_use"}},
            {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"k"'}},
            {"type": "message_stop"},
        ]
        lines = self._make_sse_lines(sse_events)

        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.__iter__ = MagicMock(return_value=iter(lines))
            mock_open.return_value = mock_resp

            chunks = list(p._sync_sse_stream({"model": "claude-sonnet-4-6", "messages": []}))

        self.assertEqual(chunks, [])

    def test_non_data_lines_ignored(self):
        p = self._make_provider()
        raw_lines = [
            b"event: content_block_delta\n",
            b"data: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"text_delta\", \"text\": \"hi\"}}\n",
            b"\n",
            b"data: {\"type\": \"message_stop\"}\n",
        ]

        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.__iter__ = MagicMock(return_value=iter(raw_lines))
            mock_open.return_value = mock_resp

            chunks = list(p._sync_sse_stream({"model": "claude-sonnet-4-6", "messages": []}))

        self.assertEqual(chunks, ["hi"])

    def test_invalid_json_skipped(self):
        p = self._make_provider()
        raw_lines = [
            b"data: not-json\n",
            b"data: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"text_delta\", \"text\": \"ok\"}}\n",
            b"data: {\"type\": \"message_stop\"}\n",
        ]

        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.__iter__ = MagicMock(return_value=iter(raw_lines))
            mock_open.return_value = mock_resp

            chunks = list(p._sync_sse_stream({"model": "claude-sonnet-4-6", "messages": []}))

        self.assertEqual(chunks, ["ok"])


class TestAgentLoopEventStream(unittest.IsolatedAsyncioTestCase):
    """Test AgentLoop.event_stream() yields correct event types."""

    def _make_loop(self, tool_calls=None, stream_mode="final_only"):
        from hushclaw.loop import AgentLoop
        from hushclaw.providers.base import LLMResponse, ToolCall
        from hushclaw.config.schema import Config, AgentConfig, ToolsConfig
        from hushclaw.runtime.hooks import HookBus

        config = Config(
            agent=AgentConfig(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                max_tool_rounds=5,
                stream_mode=stream_mode,
            ),
            tools=ToolsConfig(enabled=[], timeout=30),
        )

        # Mock provider (stream_complete=None → only complete() path, avoids double pre_llm_call)
        provider = MagicMock()
        provider.stream_complete = None
        tc_list = tool_calls or []
        if tc_list:
            # First call returns tool_use, second call returns end_turn
            provider.complete = AsyncMock(side_effect=[
                LLMResponse(
                    content="Thinking...",
                    stop_reason="tool_use",
                    tool_calls=tc_list,
                ),
                LLMResponse(
                    content="Done.",
                    stop_reason="end_turn",
                    tool_calls=[],
                ),
            ])
        else:
            provider.complete = AsyncMock(return_value=LLMResponse(
                content="Hello!",
                stop_reason="end_turn",
                tool_calls=[],
            ))

        # Mock memory
        memory = MagicMock()
        memory.recall = MagicMock(return_value="")
        memory.search_by_tag = MagicMock(return_value=[])
        memory.save_turn = MagicMock()
        memory.asave_turn = AsyncMock(return_value="turn-id")
        memory.session_log.aappend = AsyncMock(return_value="ev-id")
        memory.session_log.acomplete = AsyncMock()
        memory.session_log.afail = AsyncMock()

        # Mock registry
        registry = MagicMock()
        registry.to_api_schemas = MagicMock(return_value=[])

        # Mock executor
        from hushclaw.tools.base import ToolResult
        executor_mock = MagicMock()
        executor_mock.set_context = MagicMock()
        executor_mock.execute = AsyncMock(return_value=ToolResult.ok("tool output"))

        loop = AgentLoop.__new__(AgentLoop)
        loop.config = config
        loop.provider = provider
        loop.memory = memory
        loop.registry = registry
        loop.session_id = "s-test"
        loop.gateway = None
        loop._context = []
        loop._total_input_tokens = 0
        loop._total_output_tokens = 0
        loop._session_input_tokens = 0
        loop._session_output_tokens = 0
        loop.executor = executor_mock
        loop.pipeline_run_id = ""
        loop.hook_bus = HookBus()
        # Phase 5: SandboxManager stub (no real browser in unit tests)
        sandbox_mock = MagicMock()
        sandbox_mock.session = MagicMock()
        sandbox_mock.ensure_cdp = AsyncMock()
        sandbox_mock.close = AsyncMock()
        loop._sandbox = sandbox_mock
        loop._trajectory_writer = None  # trajectory disabled in unit tests
        # DefaultContextEngine (inline stub to avoid real memory calls)
        from hushclaw.context.engine import ContextEngine
        from hushclaw.context.policy import ContextPolicy

        class _StubEngine(ContextEngine):
            async def assemble(self, query, policy, memory, config, session_id=None, pipeline_run_id="", **kwargs):
                return ("You are HushClaw.", f"Today is 2026-01-01.")
            async def compact(self, messages, policy, provider, model, memory, session_id):
                return messages
            async def after_turn(self, session_id, user_input, assistant_response, memory):
                pass

        loop.context_engine = _StubEngine()

        return loop

    async def test_simple_response_yields_chunk_and_done(self):
        loop = self._make_loop()
        events = []
        async for ev in loop.event_stream("hello"):
            events.append(ev)

        types = [e["type"] for e in events]
        self.assertIn("chunk", types)
        self.assertIn("done", types)

        chunk_events = [e for e in events if e["type"] == "chunk"]
        self.assertEqual(chunk_events[0]["text"], "Hello!")

        done_event = next(e for e in events if e["type"] == "done")
        self.assertEqual(done_event["text"], "Hello!")

    async def test_default_stream_mode_uses_stream_complete(self):
        from hushclaw.providers.base import LLMResponse

        loop = self._make_loop()

        stream_called = []

        async def _stream_complete(**kwargs):
            stream_called.append(True)
            yield "streamed text"
            yield LLMResponse(content="streamed text", stop_reason="end_turn", tool_calls=[])

        loop.provider.stream_complete = _stream_complete
        loop.provider.complete = AsyncMock(return_value=LLMResponse(
            content="complete text",
            stop_reason="end_turn",
            tool_calls=[],
        ))

        events = []
        async for ev in loop.event_stream("hello"):
            events.append(ev)

        # With the default "final_only" mode, stream_complete is used when available
        self.assertTrue(stream_called, "stream_complete should be called for final_only mode")
        done_event = next(e for e in events if e["type"] == "done")
        self.assertEqual(done_event["text"], "streamed text")
        self.assertTrue(any(e.get("text") == "streamed text" for e in events if e["type"] == "chunk"))

    async def test_stream_fallback_after_buffered_chunk_emits_only_final_text(self):
        from hushclaw.providers.base import LLMResponse

        loop = self._make_loop()

        async def _stream_complete(**kwargs):
            yield "Partial answer."
            raise RuntimeError("stream dropped")

        loop.provider.stream_complete = _stream_complete
        loop.provider.complete = AsyncMock(return_value=LLMResponse(
            content="Complete answer.",
            stop_reason="end_turn",
            tool_calls=[],
        ))

        events = []
        async for ev in loop.event_stream("hello"):
            events.append(ev)

        chunk_texts = [e["text"] for e in events if e["type"] == "chunk"]
        self.assertEqual(chunk_texts, ["Complete answer."])
        done_event = next(e for e in events if e["type"] == "done")
        self.assertEqual(done_event["text"], "Complete answer.")

    async def test_stream_mode_never_uses_complete_not_stream(self):
        from hushclaw.providers.base import LLMResponse
        from hushclaw.config.schema import AgentConfig

        loop = self._make_loop()
        loop.config.agent = AgentConfig(stream_mode="off")

        async def _stream_complete(**kwargs):
            yield "streamed text"
            yield LLMResponse(content="", stop_reason="end_turn", tool_calls=[])

        loop.provider.stream_complete = _stream_complete
        loop.provider.complete = AsyncMock(return_value=LLMResponse(
            content="complete text",
            stop_reason="end_turn",
            tool_calls=[],
        ))

        events = []
        async for ev in loop.event_stream("hello"):
            events.append(ev)

        loop.provider.complete.assert_awaited()
        done_event = next(e for e in events if e["type"] == "done")
        self.assertEqual(done_event["text"], "complete text")
        self.assertFalse(any(e.get("text") == "streamed text" for e in events if e["type"] == "chunk"))

    async def test_compaction_event_reports_effective_message_counts(self):
        from hushclaw.config.schema import ContextPolicyConfig
        from hushclaw.context.engine import ContextEngine
        from hushclaw.providers.base import Message

        loop = self._make_loop()
        loop.config.context = ContextPolicyConfig(
            history_budget=30,
            compact_threshold=0.5,
            compact_keep_turns=1,
        )
        loop._context = [
            Message(role="user", content="old " + ("x" * 200)),
            Message(role="assistant", content="old answer"),
        ]

        class _CompactEngine(ContextEngine):
            async def assemble(self, query, policy, memory, config, session_id=None, pipeline_run_id="", **kwargs):
                return ("You are HushClaw.", "Today is 2026-01-01.")
            async def compact(self, messages, policy, provider, model, memory, session_id):
                return [Message(role="user", content="[summary]"), messages[-1]]
            async def after_turn(self, session_id, user_input, assistant_response, memory):
                pass

        loop.context_engine = _CompactEngine()

        events = []
        async for ev in loop.event_stream("new"):
            events.append(ev)

        compaction = next(e for e in events if e["type"] == "compaction")
        self.assertTrue(compaction["effective"])
        self.assertGreater(compaction["archived_messages"], 0)
        self.assertIn("before_tokens", compaction)
        self.assertIn("after_tokens", compaction)

    async def test_compaction_noop_does_not_emit_repeated_event(self):
        from hushclaw.config.schema import ContextPolicyConfig
        from hushclaw.context.engine import ContextEngine
        from hushclaw.providers.base import Message

        loop = self._make_loop()
        loop.config.context = ContextPolicyConfig(
            history_budget=20,
            compact_threshold=0.5,
            compact_keep_turns=1,
        )
        loop._context = [Message(role="user", content="old " + ("x" * 200))]

        class _NoopEngine(ContextEngine):
            def __init__(self):
                self.calls = 0
            async def assemble(self, query, policy, memory, config, session_id=None, pipeline_run_id="", **kwargs):
                return ("You are HushClaw.", "Today is 2026-01-01.")
            async def compact(self, messages, policy, provider, model, memory, session_id):
                self.calls += 1
                return messages
            async def after_turn(self, session_id, user_input, assistant_response, memory):
                pass

        engine = _NoopEngine()
        loop.context_engine = engine

        events = []
        async for ev in loop.event_stream("new"):
            events.append(ev)

        self.assertEqual(engine.calls, 1)
        self.assertNotIn("compaction", [e["type"] for e in events])

    async def test_tool_call_events_emitted(self):
        from hushclaw.providers.base import ToolCall
        tool_calls = [ToolCall(id="tc-1", name="remember", input={"content": "test"})]
        loop = self._make_loop(tool_calls=tool_calls)

        events = []
        async for ev in loop.event_stream("use a tool"):
            events.append(ev)

        types = [e["type"] for e in events]
        self.assertIn("tool_call", types)
        self.assertIn("tool_result", types)
        self.assertIn("done", types)

        tool_call_ev = next(e for e in events if e["type"] == "tool_call")
        self.assertEqual(tool_call_ev["tool"], "remember")
        self.assertEqual(tool_call_ev["input"], {"content": "test"})

        tool_result_ev = next(e for e in events if e["type"] == "tool_result")
        self.assertEqual(tool_result_ev["tool"], "remember")
        self.assertEqual(tool_result_ev["result"], "tool output")

    async def test_non_streaming_tool_preamble_is_not_final_answer_text(self):
        from hushclaw.providers.base import LLMResponse, ToolCall

        tool_call = ToolCall(id="tc-1", name="remember", input={"content": "test"})
        loop = self._make_loop()
        loop.provider.complete = AsyncMock(side_effect=[
            LLMResponse(content="Checking first.", stop_reason="tool_use", tool_calls=[tool_call]),
            LLMResponse(content="Final answer.", stop_reason="end_turn", tool_calls=[]),
        ])

        events = []
        async for ev in loop.event_stream("use a tool"):
            events.append(ev)

        chunks = [e["text"] for e in events if e["type"] == "chunk"]
        self.assertNotIn("Checking first.", chunks)
        self.assertIn("Final answer.", chunks)
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["text"], "Final answer.")

    async def test_visible_tool_step_text_does_not_duplicate_final_answer(self):
        from hushclaw.providers.base import LLMResponse, ToolCall

        tool_call = ToolCall(id="tc-1", name="web_search", input={"query": "late search"})
        loop = self._make_loop()
        loop.provider.complete = AsyncMock(side_effect=[
            LLMResponse(
                content="这是第一版完整回答，但模型同时要求继续搜索。",
                stop_reason="tool_use",
                tool_calls=[tool_call],
            ),
            LLMResponse(
                content="这是搜索后的最终回答。",
                stop_reason="end_turn",
                tool_calls=[],
            ),
        ])

        events = []
        async for ev in loop.event_stream("直接回答"):
            events.append(ev)

        self.assertTrue(any(e["type"] == "tool_call" for e in events))
        self.assertTrue(any(e["type"] == "tool_result" for e in events))
        chunks = [e["text"] for e in events if e["type"] == "chunk"]
        self.assertNotIn("这是第一版完整回答，但模型同时要求继续搜索。", chunks)
        self.assertIn("这是搜索后的最终回答。", chunks)
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["text"], "这是搜索后的最终回答。")
        self.assertEqual(done["stop_reason"], "end_turn")
        loop.executor.execute.assert_awaited()

    async def test_tool_calls_take_precedence_over_mislabeled_stop_reason(self):
        from hushclaw.providers.base import LLMResponse, ToolCall

        tool_call = ToolCall(id="tc-1", name="web_search", input={"query": "late search"})
        loop = self._make_loop()
        loop.provider.complete = AsyncMock(side_effect=[
            LLMResponse(
                content="This looks final, but still has tool calls.",
                stop_reason="end_turn",
                tool_calls=[tool_call],
            ),
            LLMResponse(
                content="Final after tool.",
                stop_reason="end_turn",
                tool_calls=[],
            ),
        ])

        events = []
        async for ev in loop.event_stream("answer after checking"):
            events.append(ev)

        self.assertTrue(any(e["type"] == "tool_call" and e["tool"] == "web_search" for e in events))
        self.assertTrue(any(e["type"] == "tool_result" for e in events))
        chunks = [e["text"] for e in events if e["type"] == "chunk"]
        self.assertNotIn("This looks final, but still has tool calls.", chunks)
        self.assertEqual(chunks[-1], "Final after tool.")
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["text"], "Final after tool.")
        loop.executor.execute.assert_awaited()

    async def test_streamed_tool_step_text_with_mislabeled_stop_reason_is_not_final(self):
        from hushclaw.providers.base import LLMResponse, ToolCall

        tool_call = ToolCall(id="tc-1", name="web_search", input={"query": "late search"})
        loop = self._make_loop(stream_mode="always")
        stream_calls = {"count": 0}

        async def _stream_complete(**kwargs):
            stream_calls["count"] += 1
            if stream_calls["count"] > 1:
                yield "Final after streamed tool."
                yield LLMResponse(
                    content="Final after streamed tool.",
                    stop_reason="end_turn",
                    tool_calls=[],
                )
                return
            yield "This streamed text looks final."
            yield LLMResponse(
                content="",
                stop_reason="end_turn",
                tool_calls=[tool_call],
            )

        loop.provider.stream_complete = _stream_complete
        loop.provider.complete = AsyncMock(return_value=LLMResponse(
            content="Final after streamed tool.",
            stop_reason="end_turn",
            tool_calls=[],
        ))

        events = []
        async for ev in loop.event_stream("answer after checking"):
            events.append(ev)

        self.assertTrue(any(e["type"] == "tool_call" and e["tool"] == "web_search" for e in events))
        chunks = [e["text"] for e in events if e["type"] == "chunk"]
        self.assertNotIn("This streamed text looks final.", chunks)
        self.assertEqual(chunks[-1], "Final after streamed tool.")
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["text"], "Final after streamed tool.")
        loop.executor.execute.assert_awaited()
        loop.provider.complete.assert_not_awaited()

    async def test_run_treats_tool_calls_as_tool_use_even_when_stop_reason_is_mislabeled(self):
        from hushclaw.providers.base import LLMResponse, ToolCall

        tool_call = ToolCall(id="tc-1", name="remember", input={"content": "test"})
        loop = self._make_loop()
        loop.provider.complete = AsyncMock(side_effect=[
            LLMResponse(
                content="Intermediate.",
                stop_reason="end_turn",
                tool_calls=[tool_call],
            ),
            LLMResponse(
                content="Final answer.",
                stop_reason="end_turn",
                tool_calls=[],
            ),
        ])

        text = await loop.run("use a tool")

        self.assertEqual(text, "Final answer.")
        loop.executor.execute.assert_awaited()

    async def test_event_stream_pauses_before_tools_when_assistant_asks_confirmation(self):
        from hushclaw.providers.base import LLMResponse, ToolCall

        loop = self._make_loop()
        loop.provider.complete = AsyncMock(return_value=LLMResponse(
            content="明白了吗？我现在就按这个逻辑构建 skill，你确认吗？还是有想补充的方向？",
            stop_reason="tool_use",
            tool_calls=[ToolCall(id="tc-1", name="remember_skill", input={"name": "x"})],
        ))

        events = []
        async for ev in loop.event_stream("先讨论这个 skill 逻辑"):
            events.append(ev)

        self.assertFalse(any(e["type"] == "tool_call" for e in events))
        self.assertFalse(any(e["type"] == "tool_result" for e in events))
        loop.executor.execute.assert_not_awaited()
        awaiting = next(e for e in events if e["type"] == "awaiting_user")
        self.assertEqual(awaiting["stop_reason"], "awaiting_user_confirmation")
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["stop_reason"], "awaiting_user_confirmation")
        self.assertIn("你确认吗", done["text"])

    async def test_event_stream_pauses_when_streamed_text_asks_confirmation_before_tool_use(self):
        from hushclaw.providers.base import LLMResponse, ToolCall

        loop = self._make_loop(stream_mode="always")
        confirmation_text = "明白了吗？我现在就按这个逻辑构建 skill，你确认吗？还是有想补充的方向？"

        async def _stream_complete(**kwargs):
            yield confirmation_text
            yield LLMResponse(
                content="",
                stop_reason="tool_use",
                tool_calls=[ToolCall(id="tc-1", name="remember_skill", input={"name": "x"})],
            )

        loop.provider.stream_complete = _stream_complete
        loop.provider.complete = AsyncMock()

        events = []
        async for ev in loop.event_stream("先讨论这个 skill 逻辑"):
            events.append(ev)

        self.assertEqual([e["type"] for e in events if e["type"] == "tool_call"], [])
        loop.executor.execute.assert_not_awaited()
        awaiting = next(e for e in events if e["type"] == "awaiting_user")
        self.assertEqual(awaiting["text"], confirmation_text)
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["stop_reason"], "awaiting_user_confirmation")
        self.assertEqual(done["text"], confirmation_text)
        loop.provider.complete.assert_not_awaited()

    async def test_event_stream_pauses_immediately_when_streamed_text_needs_user_input(self):
        from hushclaw.providers.base import LLMResponse

        loop = self._make_loop(stream_mode="always")
        confirmation_text = "我可以按这个方案继续实现。你确认吗？"
        continued = {"value": False}

        async def _stream_complete(**kwargs):
            yield confirmation_text
            continued["value"] = True
            yield "这段不应该继续输出"
            yield LLMResponse(content="", stop_reason="end_turn", tool_calls=[])

        loop.provider.stream_complete = _stream_complete
        loop.provider.complete = AsyncMock()

        events = []
        async for ev in loop.event_stream("先确认方案"):
            events.append(ev)

        self.assertFalse(continued["value"])
        self.assertEqual([e["type"] for e in events if e["type"] == "tool_call"], [])
        awaiting = next(e for e in events if e["type"] == "awaiting_user")
        self.assertEqual(awaiting["text"], confirmation_text)
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["stop_reason"], "awaiting_user_confirmation")
        self.assertEqual(done["text"], confirmation_text)
        loop.provider.complete.assert_not_awaited()

    async def test_event_stream_resumes_paused_tool_calls_after_user_confirms(self):
        from hushclaw.providers.base import LLMResponse, ToolCall

        loop = self._make_loop()
        confirmation_text = "明白了吗？我现在就按这个逻辑构建 skill，你确认吗？还是有想补充的方向？"

        loop.provider.stream_complete = None
        loop.provider.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=confirmation_text,
                stop_reason="tool_use",
                tool_calls=[ToolCall(id="tc-1", name="remember_skill", input={"name": "x"})],
            ),
            LLMResponse(
                content="Skill built.",
                stop_reason="end_turn",
                tool_calls=[],
            )
        ])

        first_events = []
        async for ev in loop.event_stream("先讨论这个 skill 逻辑"):
            first_events.append(ev)
        self.assertFalse(any(e["type"] == "tool_call" for e in first_events))

        second_events = []
        async for ev in loop.event_stream("确认"):
            second_events.append(ev)

        self.assertTrue(any(e["type"] == "tool_call" and e["tool"] == "remember_skill" for e in second_events))
        self.assertTrue(any(e["type"] == "tool_result" and e["tool"] == "remember_skill" for e in second_events))
        done = next(e for e in second_events if e["type"] == "done")
        self.assertEqual(done["text"], "Skill built.")
        loop.executor.execute.assert_awaited()

    async def test_event_stream_requires_chat_confirmation_for_x_post(self):
        from hushclaw.providers.base import LLMResponse, ToolCall

        tool_call = ToolCall(id="tc-x", name="x_post", input={"text": "Ship this"})
        loop = self._make_loop()
        loop.provider.stream_complete = None
        loop.provider.complete = AsyncMock(side_effect=[
            LLMResponse(content="", stop_reason="tool_use", tool_calls=[tool_call]),
            LLMResponse(content="Published.", stop_reason="end_turn", tool_calls=[]),
        ])

        first_events = []
        async for ev in loop.event_stream("发这条推特：Ship this"):
            first_events.append(ev)

        self.assertFalse(any(e["type"] == "tool_call" for e in first_events))
        awaiting = next(e for e in first_events if e["type"] == "awaiting_user")
        self.assertEqual(awaiting["pending_tools"], ["x_post"])
        self.assertIn("Ship this", awaiting["text"])
        self.assertIn("确认", awaiting["text"])
        loop.executor.execute.assert_not_awaited()

        second_events = []
        async for ev in loop.event_stream("确认"):
            second_events.append(ev)

        self.assertTrue(any(e["type"] == "tool_call" and e["tool"] == "x_post" for e in second_events))
        self.assertTrue(any(e["type"] == "tool_result" and e["tool"] == "x_post" for e in second_events))
        loop.executor.execute.assert_awaited()

    async def test_done_event_has_full_text(self):
        loop = self._make_loop()
        events = []
        async for ev in loop.event_stream("hi"):
            events.append(ev)

        done = next(e for e in events if e["type"] == "done")
        self.assertIn("text", done)
        self.assertIn("input_tokens", done)
        self.assertIn("output_tokens", done)

    async def test_done_event_survives_assistant_event_persist_failure(self):
        loop = self._make_loop()

        async def _aappend(_session_id, event_type, *_args, **_kwargs):
            if event_type == "assistant_message_emitted":
                raise RuntimeError("cannot commit - no transaction is active")
            return "ev-id"

        loop.memory.session_log.aappend = AsyncMock(side_effect=_aappend)

        events = []
        async for ev in loop.event_stream("hi"):
            events.append(ev)

        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["text"], "Hello!")
        self.assertEqual(done["assistant_message_id"], "")
        self.assertIn("cannot commit - no transaction is active", done["warning"])

    async def test_parallel_tool_event_persist_failure_does_not_truncate_stream(self):
        from hushclaw.providers.base import LLMResponse, ToolCall
        from hushclaw.tools.base import ToolDefinition, ToolResult

        tool_call = ToolCall(id="tc-1", name="fetch_url", input={"url": "https://example.com"})
        loop = self._make_loop(tool_calls=[tool_call])
        loop.provider.complete = AsyncMock(side_effect=[
            LLMResponse(content="Checking...", stop_reason="tool_use", tool_calls=[tool_call]),
            LLMResponse(content="Final answer.", stop_reason="end_turn", tool_calls=[]),
        ])
        loop.registry.get = MagicMock(return_value=ToolDefinition(
            name="fetch_url",
            description="Fetch URL",
            parameters={},
            fn=lambda: None,
            parallel_safe=True,
        ))
        loop.executor.execute = AsyncMock(return_value=ToolResult.ok("page content"))

        async def _aappend(_session_id, event_type, *_args, **_kwargs):
            if event_type == "tool_call_requested":
                raise RuntimeError("cannot commit - no transaction is active")
            return "ev-id"

        loop.memory.session_log.aappend = AsyncMock(side_effect=_aappend)

        events = []
        async for ev in loop.event_stream("use a tool"):
            events.append(ev)

        self.assertTrue(any(e["type"] == "tool_result" and e["result"] == "page content" for e in events))
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["text"], "Final answer.")

    async def test_recall_tool_calls_are_dispatched_in_parallel_read_lane(self):
        from hushclaw.providers.base import ToolCall
        from hushclaw.tools.base import ToolDefinition

        tool_calls = [
            ToolCall(id="tc-1", name="recall", input={"query": "topic one"}),
            ToolCall(id="tc-2", name="recall", input={"query": "topic two"}),
        ]
        loop = self._make_loop(tool_calls=tool_calls)
        loop.registry.get = MagicMock(return_value=ToolDefinition(
            name="recall",
            description="Recall memory",
            parameters={},
            fn=lambda: None,
            parallel_safe=True,
        ))

        events = []
        async for ev in loop.event_stream("recall multiple things"):
            events.append(ev)

        tool_results = [e for e in events if e["type"] == "tool_result"]
        self.assertEqual([e["call_id"] for e in tool_results], ["tc-1", "tc-2"])
        self.assertEqual(loop.executor.execute.await_count, 2)

    async def test_event_stream_persists_workspace_name_not_directory_basename(self):
        loop = self._make_loop()
        loop.memory.asave_turn = AsyncMock(return_value="turn-1")
        loop.memory.update_turn_tokens = MagicMock()

        events = []
        async for ev in loop.event_stream(
            "hello",
            workspace_dir=Path("/tmp/workflows"),
            workspace_name="Workflows",
        ):
            events.append(ev)

        self.assertTrue(any(e["type"] == "done" for e in events))
        user_call = loop.memory.asave_turn.call_args_list[0]
        assistant_call = loop.memory.asave_turn.call_args_list[-1]
        self.assertEqual(user_call.kwargs.get("workspace"), "Workflows")
        self.assertEqual(assistant_call.kwargs.get("workspace"), "Workflows")

    async def test_event_stream_recovers_when_turn_only_saves_memory(self):
        from hushclaw.loop import AgentLoop
        from hushclaw.providers.base import LLMResponse, ToolCall
        from hushclaw.config.schema import Config, AgentConfig, ToolsConfig
        from hushclaw.runtime.hooks import HookBus
        from hushclaw.context.engine import ContextEngine

        config = Config(
            agent=AgentConfig(model="claude-sonnet-4-6", max_tokens=1024, max_tool_rounds=5),
            tools=ToolsConfig(enabled=[], timeout=30),
        )
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=[
            LLMResponse(content="", stop_reason="tool_use", tool_calls=[ToolCall(id="tc-1", name="remember", input={"content": "x"})]),
            LLMResponse(content="", stop_reason="end_turn", tool_calls=[]),
            LLMResponse(content="Here is the actual answer.", stop_reason="end_turn", tool_calls=[]),
        ])
        memory = MagicMock()
        memory.recall = MagicMock(return_value="")
        memory.search_by_tag = MagicMock(return_value=[])
        memory.save_turn = MagicMock(return_value="turn-1")
        memory.asave_turn = AsyncMock(return_value="turn-1")
        memory.update_turn_tokens = MagicMock()
        memory.session_log.aappend = AsyncMock(return_value="ev-id")
        memory.session_log.acomplete = AsyncMock()
        memory.session_log.afail = AsyncMock()
        registry = MagicMock()
        registry.to_api_schemas = MagicMock(return_value=[])
        from hushclaw.tools.base import ToolResult
        executor_mock = MagicMock()
        executor_mock.set_context = MagicMock()
        executor_mock.execute = AsyncMock(return_value=ToolResult.ok("saved"))

        class _StubEngine(ContextEngine):
            async def assemble(self, query, policy, memory, config, session_id=None, pipeline_run_id="", **kwargs):
                return ("You are HushClaw.", "Today is 2026-01-01.")
            async def compact(self, messages, policy, provider, model, memory, session_id):
                return messages
            async def after_turn(self, session_id, user_input, assistant_response, memory):
                pass

        loop = AgentLoop.__new__(AgentLoop)
        loop.config = config
        loop.provider = provider
        loop.memory = memory
        loop.registry = registry
        loop.session_id = "s-test"
        loop.gateway = None
        loop._context = []
        loop._total_input_tokens = 0
        loop._total_output_tokens = 0
        loop._session_input_tokens = 0
        loop._session_output_tokens = 0
        loop.executor = executor_mock
        loop.pipeline_run_id = ""
        loop.hook_bus = HookBus()
        sandbox_mock = MagicMock()
        sandbox_mock.session = MagicMock()
        sandbox_mock.ensure_cdp = AsyncMock()
        sandbox_mock.close = AsyncMock()
        loop._sandbox = sandbox_mock
        loop._trajectory_writer = None
        loop.context_engine = _StubEngine()

        events = []
        async for ev in loop.event_stream("tell me the result"):
            events.append(ev)

        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["text"], "Here is the actual answer.")
        chunk_texts = [e["text"] for e in events if e["type"] == "chunk"]
        self.assertIn("Here is the actual answer.", chunk_texts)

    async def test_streamed_textual_tool_call_is_dispatched_not_final_body(self):
        from hushclaw.providers.base import LLMResponse, ToolCall

        loop = self._make_loop(stream_mode="always")
        dsml = (
            '<｜DSML｜tool_calls><｜DSML｜invoke name="remember">'
            '<｜DSML｜parameter name="content" string="true">hello</｜DSML｜parameter>'
            '</｜DSML｜invoke></｜DSML｜tool_calls>'
        )

        async def _stream_complete(*args, **kwargs):
            yield dsml
            yield LLMResponse(
                content="",
                stop_reason="tool_use",
                tool_calls=[ToolCall(id="dsml-test", name="remember", input={"content": "hello"})],
            )

        loop.provider.stream_complete = _stream_complete
        loop.provider.complete = AsyncMock(return_value=LLMResponse(
            content="Done.",
            stop_reason="end_turn",
            tool_calls=[],
        ))

        events = []
        async for ev in loop.event_stream("remember this"):
            events.append(ev)

        self.assertFalse(any(e["type"] == "chunk" and "DSML" in e.get("text", "") for e in events))
        tool_call = next(e for e in events if e["type"] == "tool_call")
        self.assertEqual(tool_call["tool"], "remember")
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["text"], "Done.")

    async def test_streamed_textual_tool_call_without_native_call_is_parsed(self):
        from hushclaw.providers.base import LLMResponse

        loop = self._make_loop(stream_mode="always")
        dsml = (
            '<｜DSML｜tool_calls><｜DSML｜invoke name="remember">'
            '<｜DSML｜parameter name="content" string="true">hello</｜DSML｜parameter>'
            '</｜DSML｜invoke></｜DSML｜tool_calls>'
        )

        async def _stream_complete(*args, **kwargs):
            yield dsml[:40]
            yield dsml[40:]
            yield LLMResponse(content="", stop_reason="end_turn", tool_calls=[])

        loop.provider.stream_complete = _stream_complete
        loop.provider.complete = AsyncMock(return_value=LLMResponse(
            content="Done.",
            stop_reason="end_turn",
            tool_calls=[],
        ))

        events = []
        async for ev in loop.event_stream("remember this"):
            events.append(ev)

        self.assertFalse(any(e["type"] == "chunk" and "DSML" in e.get("text", "") for e in events))
        tool_call = next(e for e in events if e["type"] == "tool_call")
        self.assertEqual(tool_call["tool"], "remember")
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(done["text"], "Done.")

    async def test_streamed_orphan_tool_tail_tags_are_not_rendered(self):
        from hushclaw.providers.base import LLMResponse

        loop = self._make_loop(stream_mode="always")

        async def _stream_complete(*args, **kwargs):
            yield "</parameter>\n</tool_calls>\n</think>\n"
            yield LLMResponse(content="", stop_reason="end_turn", tool_calls=[])

        loop.provider.stream_complete = _stream_complete

        events = []
        async for ev in loop.event_stream("normal answer"):
            events.append(ev)

        chunk_text = "".join(e.get("text", "") for e in events if e["type"] == "chunk")
        self.assertNotIn("parameter", chunk_text)
        self.assertNotIn("tool_calls", chunk_text)
        self.assertNotIn("think", chunk_text)

    async def test_event_stream_emits_lifecycle_hooks(self):
        from hushclaw.runtime.hooks import HookEvent

        loop = self._make_loop()
        seen = []

        def _record(event: HookEvent):
            seen.append(event.name)

        for name in ("pre_session_init", "pre_llm_call", "post_llm_call", "post_turn_persist"):
            loop.hook_bus.on(name, _record)

        async for _ in loop.event_stream("hi"):
            pass

        self.assertEqual(
            seen,
            ["pre_session_init", "pre_llm_call", "post_llm_call", "post_turn_persist"],
        )

    async def test_run_emits_tool_and_turn_hooks(self):
        from hushclaw.providers.base import ToolCall
        from hushclaw.runtime.hooks import HookEvent

        tool_calls = [ToolCall(id="tc-1", name="remember", input={"content": "test"})]
        loop = self._make_loop(tool_calls=tool_calls)
        seen = []

        def _record(event: HookEvent):
            seen.append(event.name)

        for name in (
            "pre_session_init",
            "pre_llm_call",
            "post_llm_call",
            "pre_tool_call",
            "post_tool_call",
            "post_turn_persist",
        ):
            loop.hook_bus.on(name, _record)

        result = await loop.run("use a tool")

        self.assertEqual(result, "Done.")
        self.assertEqual(
            seen,
            [
                "pre_session_init",
                "pre_llm_call",
                "post_llm_call",
                "pre_tool_call",
                "post_tool_call",
                "pre_llm_call",
                "post_llm_call",
                "post_turn_persist",
            ],
        )

    async def test_run_pauses_before_tools_when_assistant_asks_confirmation(self):
        from hushclaw.providers.base import LLMResponse, ToolCall

        loop = self._make_loop()
        loop.provider.complete = AsyncMock(return_value=LLMResponse(
            content="Please confirm before I continue.",
            stop_reason="tool_use",
            tool_calls=[ToolCall(id="tc-1", name="remember_skill", input={"name": "x"})],
        ))

        result = await loop.run("draft a skill")

        self.assertEqual(result, "Please confirm before I continue.")
        loop.executor.execute.assert_not_awaited()

    async def test_run_requires_chat_confirmation_for_x_post(self):
        from hushclaw.providers.base import LLMResponse, ToolCall

        loop = self._make_loop()
        loop.provider.complete = AsyncMock(return_value=LLMResponse(
            content="",
            stop_reason="tool_use",
            tool_calls=[ToolCall(id="tc-x", name="x_post", input={"text": "Ship this"})],
        ))

        result = await loop.run("发这条推特：Ship this")

        self.assertIn("Ship this", result)
        self.assertIn("确认", result)
        loop.executor.execute.assert_not_awaited()

    async def test_run_recovers_when_turn_only_saves_memory(self):
        from hushclaw.loop import AgentLoop
        from hushclaw.providers.base import LLMResponse, ToolCall
        from hushclaw.config.schema import Config, AgentConfig, ToolsConfig
        from hushclaw.runtime.hooks import HookBus
        from hushclaw.context.engine import ContextEngine

        config = Config(
            agent=AgentConfig(model="claude-sonnet-4-6", max_tokens=1024, max_tool_rounds=5),
            tools=ToolsConfig(enabled=[], timeout=30),
        )
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=[
            LLMResponse(content="", stop_reason="tool_use", tool_calls=[ToolCall(id="tc-1", name="remember", input={"content": "x"})]),
            LLMResponse(content="", stop_reason="end_turn", tool_calls=[]),
            LLMResponse(content="Final user-facing answer.", stop_reason="end_turn", tool_calls=[]),
        ])
        memory = MagicMock()
        memory.recall = MagicMock(return_value="")
        memory.search_by_tag = MagicMock(return_value=[])
        memory.save_turn = MagicMock(return_value="turn-1")
        memory.asave_turn = AsyncMock(return_value="turn-1")
        memory.session_log.aappend = AsyncMock(return_value="ev-id")
        memory.session_log.acomplete = AsyncMock()
        memory.session_log.afail = AsyncMock()
        registry = MagicMock()
        registry.to_api_schemas = MagicMock(return_value=[])
        from hushclaw.tools.base import ToolResult
        executor_mock = MagicMock()
        executor_mock.set_context = MagicMock()
        executor_mock.execute = AsyncMock(return_value=ToolResult.ok("saved"))

        class _StubEngine(ContextEngine):
            async def assemble(self, query, policy, memory, config, session_id=None, pipeline_run_id="", **kwargs):
                return ("You are HushClaw.", "Today is 2026-01-01.")
            async def compact(self, messages, policy, provider, model, memory, session_id):
                return messages
            async def after_turn(self, session_id, user_input, assistant_response, memory):
                pass

        loop = AgentLoop.__new__(AgentLoop)
        loop.config = config
        loop.provider = provider
        loop.memory = memory
        loop.registry = registry
        loop.session_id = "s-test"
        loop.gateway = None
        loop._context = []
        loop._total_input_tokens = 0
        loop._total_output_tokens = 0
        loop._session_input_tokens = 0
        loop._session_output_tokens = 0
        loop.executor = executor_mock
        loop.pipeline_run_id = ""
        loop.hook_bus = HookBus()
        sandbox_mock = MagicMock()
        sandbox_mock.session = MagicMock()
        sandbox_mock.ensure_cdp = AsyncMock()
        sandbox_mock.close = AsyncMock()
        loop._sandbox = sandbox_mock
        loop._trajectory_writer = None
        loop.context_engine = _StubEngine()

        out = await loop.run("tell me the result")
        self.assertEqual(out, "Final user-facing answer.")


if __name__ == "__main__":
    unittest.main()
