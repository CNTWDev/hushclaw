"""Tests for ContextPolicy and DefaultContextEngine."""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hushclaw.agent import Agent
from hushclaw.learning.controller import LearningController
from hushclaw.context.policy import ContextPolicy
from hushclaw.context.engine import DefaultContextEngine, detect_response_mode, needs_compaction, should_auto_recall
from hushclaw.runtime.hooks import HookEvent
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

    def test_zero_history_budget_disables_compaction(self):
        p = ContextPolicy(history_budget=0, compact_threshold=0.5)
        msgs = [Message(role="user", content="x" * 100) for _ in range(50)]
        assert not needs_compaction(msgs, p)


# ---------------------------------------------------------------------------
# DefaultContextEngine.assemble tests
# ---------------------------------------------------------------------------

class TestDefaultContextEngineAssemble:
    def _make_engine_and_deps(self):
        engine = DefaultContextEngine()
        memory = MagicMock()
        memory.recall_with_budget = MagicMock(return_value="")
        memory.user_profile.render_profile_context = MagicMock(return_value="")
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
        assert "[TZ] User's timezone:" in dynamic

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

    def test_relative_day_anchors_follow_configured_timezone(self):
        engine = DefaultContextEngine(calendar_timezone="Asia/Shanghai")
        anchors = engine._build_relative_day_anchors(
            datetime.fromisoformat("2026-04-20T00:30:00+08:00")
        )
        assert anchors["yesterday_date"] == "2026-04-19"
        assert anchors["today_date"] == "2026-04-20"
        assert anchors["tomorrow_date"] == "2026-04-21"
        assert anchors["today_from_utc"] == "2026-04-19T16:00:00Z"
        assert anchors["today_to_utc"] == "2026-04-20T16:00:00Z"

    def test_resolve_effective_timezone_falls_back_to_local_tz(self, monkeypatch):
        fake_local = datetime.fromisoformat("2026-04-20T00:30:00+08:00")

        class FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fake_local.replace(tzinfo=None)
                return fake_local.astimezone(tz)

        monkeypatch.setattr("hushclaw.context.engine.datetime", FakeDateTime)
        engine = DefaultContextEngine(calendar_timezone="")
        tzinfo, tz_name = engine._resolve_effective_timezone()

        assert tzinfo is not None
        assert tz_name
        assert tz_name != "UTC"

    def test_working_state_injected_when_present(self):
        engine, memory, config = self._make_engine_and_deps()
        memory.recall_with_budget = MagicMock(return_value="")
        memory.user_profile.render_profile_context = MagicMock(return_value="")
        memory.load_session_working_state = MagicMock(
            return_value="### Active Goal\nFinish the context durability work"
        )
        policy = ContextPolicy()
        _, dynamic = asyncio.run(
            engine.assemble("hello", policy, memory, config, session_id="s-working")
        )
        assert "Active Working State" in dynamic
        assert "Finish the context durability work" in dynamic

    def test_short_operational_query_skips_auto_recall_when_working_state_exists(self):
        engine, memory, config = self._make_engine_and_deps()
        memory.user_profile.render_profile_context = MagicMock(return_value="")
        memory.load_session_working_state = MagicMock(
            return_value="### Goal\nFinish the current bugfix"
        )
        policy = ContextPolicy()
        _, dynamic = asyncio.run(
            engine.assemble("继续", policy, memory, config, session_id="s-working")
        )
        memory.recall_with_budget.assert_not_called()
        assert "Relevant memories" not in dynamic

    def test_history_or_preference_query_still_auto_recalls(self):
        engine, memory, config = self._make_engine_and_deps()
        memory.user_profile.render_profile_context = MagicMock(return_value="")
        memory.load_session_working_state = MagicMock(
            return_value="### Goal\nKeep the coding flow moving"
        )
        memory.recall_with_budget = MagicMock(
            return_value="[Preference]\nThe user prefers concise Chinese answers."
        )
        policy = ContextPolicy()
        _, dynamic = asyncio.run(
            engine.assemble(
                "还记得我之前偏好的回复风格吗？",
                policy,
                memory,
                config,
                session_id="s-working",
            )
        )
        memory.recall_with_budget.assert_called_once()
        assert "Recalled memories" in dynamic
        assert "prefers concise Chinese answers" in dynamic

    def test_profile_snapshot_injected_when_present(self):
        engine, memory, config = self._make_engine_and_deps()
        memory.user_profile.render_profile_context = MagicMock(
            return_value="### Communication Style\n- response_depth: User prefers concise answers."
        )
        memory.load_session_working_state = MagicMock(return_value=None)
        policy = ContextPolicy()
        _, dynamic = asyncio.run(engine.assemble("hello", policy, memory, config, session_id="s1"))
        assert "User Profile Snapshot" in dynamic
        assert "User prefers concise answers" in dynamic

    def test_discussion_mode_hint_injected_for_thinking_aloud_turn(self):
        engine, memory, config = self._make_engine_and_deps()
        memory.user_profile.render_profile_context = MagicMock(return_value="")
        memory.load_session_working_state = MagicMock(
            return_value="### Goal\nRefine the agent architecture"
        )
        policy = ContextPolicy()
        _, dynamic = asyncio.run(
            engine.assemble(
                "我觉得前期讨论的时候，系统应该先轻一点，不要每次都长篇大论。",
                policy,
                memory,
                config,
                session_id="s-discussion",
            )
        )
        assert "[RESPONSE MODE] Discussion mode." in dynamic

    def test_synthesis_mode_hint_injected_for_explicit_wrap_up(self):
        engine, memory, config = self._make_engine_and_deps()
        memory.user_profile.render_profile_context = MagicMock(return_value="")
        memory.load_session_working_state = MagicMock(
            return_value="### Goal\nRefine the agent architecture"
        )
        policy = ContextPolicy()
        _, dynamic = asyncio.run(
            engine.assemble(
                "现在请结合前面的讨论，系统梳理一下最终方案。",
                policy,
                memory,
                config,
                session_id="s-synthesis",
            )
        )
        assert "[RESPONSE MODE] Synthesis mode." in dynamic


class TestAutoRecallHeuristics:
    def test_auto_recall_disabled_for_short_operational_query_with_working_state(self):
        assert not should_auto_recall("跑测试", has_working_state=True)

    def test_auto_recall_enabled_for_history_query_with_working_state(self):
        assert should_auto_recall("我们之前决定用什么方案？", has_working_state=True)

    def test_auto_recall_enabled_without_working_state(self):
        assert should_auto_recall("继续修这个问题", has_working_state=False)


class TestResponseModeHeuristics:
    def test_detect_response_mode_discussion_for_statement(self):
        assert detect_response_mode(
            "我觉得这个阶段先轻对话，最后再梳理会更自然。",
            has_working_state=True,
        ) == "discussion"

    def test_detect_response_mode_synthesis_for_explicit_summary(self):
        assert detect_response_mode(
            "你现在系统梳理一下最终方案。",
            has_working_state=True,
        ) == "synthesis"

    def test_detect_response_mode_default_for_operational_turn(self):
        assert detect_response_mode("继续", has_working_state=True) == "default"


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

    def test_compact_reinjects_working_state_when_present(self):
        engine = DefaultContextEngine()
        policy = ContextPolicy(compact_keep_turns=2, compact_strategy="lossless")
        msgs = self._make_messages(8)
        memory = MagicMock()
        memory.load_session_working_state = MagicMock(
            return_value="### Active Goal\nPreserve state across compaction"
        )
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=LLMResponse(
            content="Old context summary",
            stop_reason="end_turn",
        ))
        result = asyncio.run(engine.compact(msgs, policy, provider, "model", memory, "sess"))
        assert any("Working state" in str(m.content) for m in result)


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

    def test_after_turn_skips_markdown_ppt_fragment(self):
        """Regex '生成了' must not persist markdown debris like ** PPT。"""
        engine = DefaultContextEngine(auto_extract=True)
        memory = MagicMock()
        asyncio.run(engine.after_turn(
            "sess-1",
            "",
            "已为您生成了 ** PPT。",
            memory,
        ))
        memory.remember.assert_not_called()

    def test_after_turn_saves_url(self):
        engine = DefaultContextEngine(auto_extract=True)
        memory = MagicMock()
        memory.note_exists_with_title.return_value = False
        asyncio.run(engine.after_turn(
            "sess-1",
            "",
            "See https://example.com/doc for details.",
            memory,
        ))
        memory.remember.assert_called_once()
        args, kwargs = memory.remember.call_args
        assert "example.com" in args[0]
        assert kwargs.get("memory_kind") == "project_knowledge"

    def test_after_turn_skips_save_to_memory_phrase(self):
        engine = DefaultContextEngine(auto_extract=True)
        memory = MagicMock()
        asyncio.run(engine.after_turn(
            "sess-1",
            "",
            "好的，我会并保存到记忆中。",
            memory,
        ))
        memory.remember.assert_not_called()

    def test_after_turn_skips_request_like_task_memory(self):
        engine = DefaultContextEngine(auto_extract=True)
        memory = MagicMock()
        asyncio.run(engine.after_turn(
            "sess-1",
            "我需要：整理尼日利亚市场周报并输出关键结论。",
            "已完成，我们采用策略A，后续继续执行。",
            memory,
        ))
        memory.remember.assert_not_called()

    def test_after_turn_extracts_preference_and_decision(self):
        engine = DefaultContextEngine(auto_extract=True)
        memory = MagicMock()
        memory.note_exists_with_title.return_value = False
        asyncio.run(engine.after_turn(
            "sess-1",
            "我喜欢简洁直接的回答。我们采用双阶段发布方案。",
            "",
            memory,
        ))
        assert memory.remember.call_count == 2
        calls = memory.remember.call_args_list
        saved = [(c.args[0], c.kwargs.get("note_type"), c.kwargs.get("memory_kind")) for c in calls]
        assert ("简洁直接的回答", "preference", "user_model") in saved
        assert ("双阶段发布方案", "decision", "decision") in saved

    def test_after_turn_extracts_user_interest_question(self):
        engine = DefaultContextEngine(auto_extract=True)
        memory = MagicMock()
        memory.note_exists_with_title.return_value = False
        asyncio.run(engine.after_turn(
            "sess-1",
            "为什么尼日利亚用户更喜欢轻量论坛式的信息流？",
            "",
            memory,
        ))
        memory.remember.assert_called_once()
        args, kwargs = memory.remember.call_args
        assert "尼日利亚用户更喜欢轻量论坛式的信息流" in args[0]
        assert kwargs.get("note_type") == "interest"
        assert kwargs.get("memory_kind") == "user_model"


class TestWorkingStateBuilder:
    def test_build_working_state_uses_structured_sections(self):
        messages = [
            Message(role="user", content="Find session lineage regressions in the new UI"),
            Message(role="assistant", content="I inspected the sidebar flow. Next I will wire the lineage details into session history."),
            Message(role="tool", content="Updated websocket handler and history payload", tool_name="apply_patch"),
        ]
        text = Agent._build_working_state(messages)
        assert "### Goal" in text
        assert "### Progress" in text
        assert "### Open Loops" in text
        assert "### Recent Tool Outputs" in text
        assert "Find session lineage regressions" in text
        assert "apply_patch" in text


class TestLearningController:
    def test_learning_controller_records_reflection_and_profile(self):
        memory = MagicMock()
        memory.record_reflection = MagicMock(return_value="refl-1")
        memory.record_skill_outcome = MagicMock(return_value="sko-1")
        memory.user_profile.upsert_fact = MagicMock(return_value="upf-1")
        ctl = LearningController(memory)
        ctl.on_pre_session_init(HookEvent(name="pre_session_init", payload={"session_id": "sess-1"}))
        ctl.on_post_tool_call(HookEvent(
            name="post_tool_call",
            payload={
                "session_id": "sess-1",
                "tool_name": "fetch_url",
                "tool_input": {"url": "https://example.com"},
                "tool_result": "ok",
                "is_error": False,
            },
        ))
        ctl.on_post_tool_call(HookEvent(
            name="post_tool_call",
            payload={
                "session_id": "sess-1",
                "tool_name": "jina_read",
                "tool_input": {"url": "https://example.com"},
                "tool_result": "ok",
                "is_error": False,
            },
        ))
        ctl.on_post_tool_call(HookEvent(
            name="post_tool_call",
            payload={
                "session_id": "sess-1",
                "tool_name": "remember",
                "tool_input": {"content": "x"},
                "tool_result": "saved",
                "is_error": False,
            },
        ))
        asyncio.run(ctl.on_post_turn_persist(HookEvent(
            name="post_turn_persist",
            payload={
                "session_id": "sess-1",
                "user_input": "Please keep answers concise.",
                "assistant_response": "Here is the result.",
                "workspace": "",
            },
        )))
        memory.record_reflection.assert_called_once()
        memory.user_profile.upsert_fact.assert_called_once()

    def test_learning_controller_auto_patches_single_skill_on_correction_signal(self):
        memory = MagicMock()
        memory.record_reflection = MagicMock(return_value="refl-1")
        memory.record_skill_outcome = MagicMock(return_value="sko-1")
        memory.user_profile.upsert_fact = MagicMock(return_value="upf-1")
        memory.list_skill_outcomes = MagicMock(return_value=[])
        skill_manager = MagicMock()
        skill_manager.get.return_value = {
            "name": "deep-research",
            "tier": "user",
            "content": "## Workflow\n- Gather sources",
        }
        ctl = LearningController(memory, skill_manager=skill_manager)
        ctl.on_pre_session_init(HookEvent(name="pre_session_init", payload={"session_id": "sess-2"}))
        ctl.on_post_tool_call(HookEvent(
            name="post_tool_call",
            payload={
                "session_id": "sess-2",
                "tool_name": "use_skill",
                "tool_input": {"name": "deep-research"},
                "tool_result": "loaded",
                "is_error": False,
            },
        ))
        asyncio.run(ctl.on_post_turn_persist(HookEvent(
            name="post_turn_persist",
            payload={
                "session_id": "sess-2",
                "user_input": "不是这个方向，请更关注真实用户评价。",
                "assistant_response": "已调整。",
                "workspace": "",
            },
        )))
        skill_manager.patch.assert_called_once()
