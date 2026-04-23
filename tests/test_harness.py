"""Tests for HarnessFactory cold-start rebuild from thread events."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


def _make_agent(tmpdir: Path):
    """Create a minimal Agent backed by a real (in-memory) MemoryStore."""
    from hushclaw.config.schema import (
        Config, AgentConfig, ProviderConfig, MemoryConfig,
        ToolsConfig, LoggingConfig, ContextPolicyConfig, ServerConfig,
    )
    from hushclaw.memory.store import MemoryStore
    from hushclaw.agent import Agent

    config = Config(
        agent=AgentConfig(model="claude-sonnet-4-6"),
        provider=ProviderConfig(name="anthropic-raw"),
        memory=MemoryConfig(data_dir=tmpdir),
        tools=ToolsConfig(enabled=[]),
        logging=LoggingConfig(),
        context=ContextPolicyConfig(),
        server=ServerConfig(),
    )
    memory = MemoryStore(data_dir=tmpdir)
    provider_mock = MagicMock()
    provider_mock.stream_complete = None
    provider_mock.complete = AsyncMock()

    agent = Agent.__new__(Agent)
    agent.config = config
    agent.memory = memory
    agent.provider = provider_mock
    from hushclaw.runtime.hooks import HookBus
    agent.hook_bus = HookBus()
    agent.hook_bus.on = MagicMock()
    agent.context_engine = None
    agent._projection_worker = None
    agent._scheduler = None

    from hushclaw.tools.registry import ToolRegistry
    agent.registry = ToolRegistry()

    from hushclaw.skills.manager import SkillManager
    from hushclaw.skills.loader import SkillRegistry
    from hushclaw.skills.installer import SkillInstaller
    from hushclaw.skills.validator import SkillValidator
    agent._skill_registry = SkillRegistry([])
    agent._skill_manager = SkillManager(
        registry=agent._skill_registry,
        installer=SkillInstaller(),
        validator=SkillValidator(),
        install_dir=None,
        tool_registry=agent.registry,
    )

    from hushclaw.learning.controller import LearningController
    agent._learning = LearningController(
        memory,
        skill_manager=agent._skill_manager,
        provider=provider_mock,
        agent_config=config.agent,
    )
    return agent


class TestHarnessFactoryRebuild(unittest.TestCase):
    def test_rebuild_from_thread_restores_session(self):
        """HarnessFactory.rebuild_from_thread() produces a loop with the correct session_id."""
        from hushclaw.runtime.harness import HarnessFactory

        with tempfile.TemporaryDirectory() as tmpdir:
            agent = _make_agent(Path(tmpdir))
            memory = agent.memory

            # Create a session with one turn
            session_id = "s-test-rebuild"
            thread_id = memory.get_or_create_thread(session_id, agent_name="test-agent")
            memory.save_turn(session_id, "user", "hello", input_tokens=10, output_tokens=0)
            memory.save_turn(session_id, "assistant", "hi there", input_tokens=0, output_tokens=5)

            loop = HarnessFactory.rebuild_from_thread(thread_id, agent)

            self.assertEqual(loop.session_id, session_id)
            # Turns loaded into context (tool turns skipped)
            roles = [m.role for m in loop._context]
            self.assertIn("user", roles)
            self.assertIn("assistant", roles)

    def test_rebuild_recovers_token_counters(self):
        """Cold-start rebuild recovers session token totals from turns table."""
        from hushclaw.runtime.harness import HarnessFactory

        with tempfile.TemporaryDirectory() as tmpdir:
            agent = _make_agent(Path(tmpdir))
            memory = agent.memory

            session_id = "s-test-tokens"
            thread_id = memory.get_or_create_thread(session_id, agent_name="test-agent")
            memory.save_turn(session_id, "user", "q1", input_tokens=100, output_tokens=0)
            memory.save_turn(session_id, "assistant", "a1", input_tokens=0, output_tokens=200)
            memory.save_turn(session_id, "user", "q2", input_tokens=50, output_tokens=0)
            memory.save_turn(session_id, "assistant", "a2", input_tokens=0, output_tokens=75)

            loop = HarnessFactory.rebuild_from_thread(thread_id, agent)

            self.assertEqual(loop._session_input_tokens, 150)
            self.assertEqual(loop._session_output_tokens, 275)

    def test_rebuild_raises_on_unknown_thread(self):
        """ValueError raised when thread_id does not exist."""
        from hushclaw.runtime.harness import HarnessFactory

        with tempfile.TemporaryDirectory() as tmpdir:
            agent = _make_agent(Path(tmpdir))
            with self.assertRaises(ValueError):
                HarnessFactory.rebuild_from_thread("t-nonexistent", agent)


if __name__ == "__main__":
    unittest.main()
