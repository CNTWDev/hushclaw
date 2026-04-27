"""Tests for SSE streaming in AnthropicRawProvider and AgentLoop.event_stream."""
from __future__ import annotations

import asyncio
import json
import unittest
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

    def _make_loop(self, tool_calls=None):
        from hushclaw.loop import AgentLoop
        from hushclaw.providers.base import LLMResponse, ToolCall
        from hushclaw.config.schema import Config, AgentConfig, ToolsConfig
        from hushclaw.runtime.hooks import HookBus

        config = Config(
            agent=AgentConfig(model="claude-sonnet-4-6", max_tokens=1024, max_tool_rounds=5),
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

    async def test_done_event_has_full_text(self):
        loop = self._make_loop()
        events = []
        async for ev in loop.event_stream("hi"):
            events.append(ev)

        done = next(e for e in events if e["type"] == "done")
        self.assertIn("text", done)
        self.assertIn("input_tokens", done)
        self.assertIn("output_tokens", done)

    async def test_event_stream_persists_workspace_name_not_directory_basename(self):
        loop = self._make_loop()
        loop.memory.save_turn = MagicMock(return_value="turn-1")
        loop.memory.update_turn_tokens = MagicMock()

        events = []
        async for ev in loop.event_stream(
            "hello",
            workspace_dir=Path("/tmp/workflows"),
            workspace_name="Workflows",
        ):
            events.append(ev)

        self.assertTrue(any(e["type"] == "done" for e in events))
        user_call = loop.memory.save_turn.call_args_list[0]
        assistant_call = loop.memory.save_turn.call_args_list[-1]
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
        memory.update_turn_tokens = MagicMock()
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
