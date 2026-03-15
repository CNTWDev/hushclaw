"""Tests for ContextPolicy and DefaultContextEngine."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hushclaw.context.policy import ContextPolicy
from hushclaw.context.engine import DefaultContextEngine, needs_compaction
from hushclaw.providers.base import LLMResponse, Message


# ---------------------------------------------------------------------------
# ContextPolicy tests
# ---------------------------------------------------------------------------

class TestContextPolicy:
    def test_defaults(self):
        p = ContextPolicy()
        assert p.stable_budget == 1_500
        assert p.dynamic_budget == 2_500
        assert p.history_budget == 60_000
        assert p.compact_threshold == 0.85
        assert p.compact_keep_turns == 6
        assert p.compact_strategy == "lossless"
        assert p.memory_min_score == 0.25
        assert p.memory_max_tokens == 800

    def test_custom_values(self):
        p = ContextPolicy(stable_budget=500, memory_min_score=0.5)
        assert p.stable_budget == 500
        assert p.memory_min_score == 0.5


# ---------------------------------------------------------------------------
# needs_compaction tests
# ---------------------------------------------------------------------------

class TestNeedsCompaction:
    def test_empty_messages_no_compact(self):
        p = ContextPolicy(history_budget=1000, compact_threshold=0.8)
        assert not needs_compaction([], p)

    def test_small_context_no_compact(self):
        p = ContextPolicy(history_budget=100_000, compact_threshold=0.85)
        msgs = [Message(role="user", content="hello")]
        assert not needs_compaction(msgs, p)

    def test_huge_context_triggers_compact(self):
        p = ContextPolicy(history_budget=10, compact_threshold=0.5)
        # Lots of messages to exceed the tiny budget
        msgs = [Message(role="user", content="x" * 100) for _ in range(50)]
        assert needs_compaction(msgs, p)


# ---------------------------------------------------------------------------
# DefaultContextEngine.assemble tests
# ---------------------------------------------------------------------------

class TestDefaultContextEngineAssemble:
    def _make_engine_and_deps(self):
        engine = DefaultContextEngine()
        memory = MagicMock()
        memory.recall_with_budget = MagicMock(return_value="")
        from hushclaw.config.schema import AgentConfig
        config = AgentConfig(
            system_prompt="You are HushClaw, a helpful AI assistant.",
            instructions="",
        )
        return engine, memory, config

    def test_assemble_returns_tuple(self):
        engine, memory, config = self._make_engine_and_deps()
        policy = ContextPolicy()
        stable, dynamic = asyncio.run(engine.assemble("hello", policy, memory, config))
        assert isinstance(stable, str)
        assert isinstance(dynamic, str)

    def test_stable_prefix_contains_role(self):
        engine, memory, config = self._make_engine_and_deps()
        policy = ContextPolicy()
        stable, _ = asyncio.run(engine.assemble("hello", policy, memory, config))
        assert "HushClaw" in stable

    def test_dynamic_suffix_contains_date(self):
        engine, memory, config = self._make_engine_and_deps()
        policy = ContextPolicy()
        _, dynamic = asyncio.run(engine.assemble("hello", policy, memory, config))
        assert "Today is" in dynamic

    def test_stable_does_not_contain_date(self):
        engine, memory, config = self._make_engine_and_deps()
        policy = ContextPolicy()
        stable, _ = asyncio.run(engine.assemble("hello", policy, memory, config))
        assert "{date}" not in stable

    def test_instructions_in_stable(self):
        engine, memory, _ = self._make_engine_and_deps()
        from hushclaw.config.schema import AgentConfig
        config = AgentConfig(
            system_prompt="You are HushClaw.",
            instructions="Always reply in Chinese.",
        )
        policy = ContextPolicy()
        stable, _ = asyncio.run(engine.assemble("hello", policy, memory, config))
        assert "Always reply in Chinese." in stable

    def test_memories_injected_when_present(self):
        engine, memory, config = self._make_engine_and_deps()
        memory.recall_with_budget = MagicMock(return_value="[HushClaw intro]\nPython AI agent")
        policy = ContextPolicy()
        _, dynamic = asyncio.run(engine.assemble("hello", policy, memory, config))
        assert "HushClaw intro" in dynamic

    def test_memories_skipped_when_empty(self):
        engine, memory, config = self._make_engine_and_deps()
        memory.recall_with_budget = MagicMock(return_value="")
        policy = ContextPolicy()
        _, dynamic = asyncio.run(engine.assemble("hello", policy, memory, config))
        assert "Relevant memories" not in dynamic


# ---------------------------------------------------------------------------
# DefaultContextEngine.compact tests
# ---------------------------------------------------------------------------

class TestDefaultContextEngineCompact:
    def _make_messages(self, n: int) -> list[Message]:
        msgs = []
        for i in range(n):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append(Message(role=role, content=f"Message {i}"))
        return msgs

    def test_compact_fewer_than_keep_turns_returns_unchanged(self):
        engine = DefaultContextEngine()
        policy = ContextPolicy(compact_keep_turns=6)
        msgs = self._make_messages(3)
        memory = MagicMock()
        provider = MagicMock()
        result = asyncio.run(engine.compact(msgs, policy, provider, "model", memory, "sess"))
        assert result == msgs

    def test_compact_lossless_archives_to_memory(self):
        engine = DefaultContextEngine()
        policy = ContextPolicy(compact_keep_turns=2, compact_strategy="lossless")
        msgs = self._make_messages(6)
        memory = MagicMock()
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=LLMResponse(
            content="Summary of old context",
            stop_reason="end_turn",
        ))
        result = asyncio.run(engine.compact(msgs, policy, provider, "model", memory, "sess"))
        # Should have archived old messages
        memory.remember.assert_called_once()
        call_kwargs = memory.remember.call_args[1]
        assert "_compact_archive" in call_kwargs.get("tags", [])

    def test_compact_result_is_smaller(self):
        engine = DefaultContextEngine()
        policy = ContextPolicy(compact_keep_turns=2, compact_strategy="lossless")
        msgs = self._make_messages(8)
        memory = MagicMock()
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=LLMResponse(
            content="Bullet summary",
            stop_reason="end_turn",
        ))
        result = asyncio.run(engine.compact(msgs, policy, provider, "model", memory, "sess"))
        assert len(result) < len(msgs)

    def test_compact_keeps_recent_turns(self):
        engine = DefaultContextEngine()
        policy = ContextPolicy(compact_keep_turns=2, compact_strategy="lossless")
        msgs = self._make_messages(6)
        memory = MagicMock()
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=LLMResponse(
            content="Old context summary",
            stop_reason="end_turn",
        ))
        result = asyncio.run(engine.compact(msgs, policy, provider, "model", memory, "sess"))
        # Recent turns should be preserved
        assert any("Message 4" in str(m.content) or "Message 5" in str(m.content) for m in result)


# ---------------------------------------------------------------------------
# DefaultContextEngine.after_turn tests
# ---------------------------------------------------------------------------

class TestDefaultContextEngineAfterTurn:
    def test_after_turn_is_noop(self):
        """after_turn should not call any memory methods (no extra LLM calls)."""
        engine = DefaultContextEngine()
        memory = MagicMock()
        asyncio.run(engine.after_turn("sess-1", "hello", "hi there", memory))
        memory.assert_not_called()
