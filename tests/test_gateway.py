"""Unit tests for Gateway and AgentPool."""
from __future__ import annotations

import asyncio
import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from hushclaw.config.schema import (
    AgentDefinition, Config, GatewayConfig, ServerConfig,
    AgentConfig, ProviderConfig, MemoryConfig, ToolsConfig,
    LoggingConfig, ContextPolicyConfig,
)


def _make_config(**gateway_kwargs) -> Config:
    return Config(
        agent=AgentConfig(model="claude-sonnet-4-6"),
        provider=ProviderConfig(name="anthropic-raw"),
        memory=MemoryConfig(),
        tools=ToolsConfig(enabled=[]),
        logging=LoggingConfig(),
        context=ContextPolicyConfig(),
        gateway=GatewayConfig(**gateway_kwargs),
        server=ServerConfig(),
    )


def _make_mock_agent(name="default"):
    agent = MagicMock()
    agent.config = _make_config()
    agent.memory = MagicMock()
    agent.registry = MagicMock()
    agent.registry.__len__ = MagicMock(return_value=0)
    agent.enable_agent_tools = MagicMock()

    mock_loop = MagicMock()
    mock_loop.run = AsyncMock(return_value=f"response from {name}")
    mock_loop.event_stream = MagicMock()

    async def _event_gen(text, **kwargs):
        yield {"type": "chunk", "text": f"hello from {name}"}
        yield {"type": "done", "text": f"hello from {name}", "input_tokens": 1, "output_tokens": 1}

    mock_loop.event_stream = _event_gen
    agent.new_loop = MagicMock(return_value=mock_loop)
    return agent


class TestAgentPool(unittest.IsolatedAsyncioTestCase):

    async def test_execute_returns_response(self):
        from hushclaw.gateway import AgentPool
        agent = _make_mock_agent("test")
        pool = AgentPool(agent, "test", max_concurrent=5)
        result = await pool.execute("hello", session_id="s-001")
        self.assertEqual(result, "response from test")

    async def test_session_affinity(self):
        """Same session_id should reuse the same AgentLoop."""
        from hushclaw.gateway import AgentPool
        agent = _make_mock_agent("test")
        pool = AgentPool(agent, "test", max_concurrent=5)

        await pool.execute("msg1", session_id="s-aaa")
        await pool.execute("msg2", session_id="s-aaa")

        # new_loop should only be called once for the same session_id
        self.assertEqual(agent.new_loop.call_count, 1)

    async def test_different_sessions_create_different_loops(self):
        """Different session_ids should get different AgentLoops."""
        from hushclaw.gateway import AgentPool
        agent = _make_mock_agent("test")
        pool = AgentPool(agent, "test", max_concurrent=5)

        await pool.execute("msg1", session_id="s-aaa")
        await pool.execute("msg2", session_id="s-bbb")

        self.assertEqual(agent.new_loop.call_count, 2)

    async def test_event_stream_yields_events(self):
        from hushclaw.gateway import AgentPool
        agent = _make_mock_agent("evtest")
        pool = AgentPool(agent, "evtest", max_concurrent=5)
        events = []
        async for ev in pool.event_stream("hello", session_id="s-ev1"):
            events.append(ev)
        self.assertTrue(any(e["type"] == "chunk" for e in events))
        self.assertTrue(any(e["type"] == "done" for e in events))


class TestSessionRestore(unittest.TestCase):
    """Agent.new_loop hydrates _context from MemoryStore when resuming a session."""

    def test_new_loop_hydrates_context_from_memory(self):
        from hushclaw.agent import Agent

        d = tempfile.mkdtemp()
        cfg = _make_config()
        cfg = dataclasses.replace(cfg, memory=dataclasses.replace(cfg.memory, data_dir=Path(d)))
        agent = Agent(config=cfg)
        try:
            sid = "sess-hydrate-001"
            agent.memory.save_turn(sid, "user", "First message")
            agent.memory.save_turn(sid, "assistant", "First reply")
            loop = agent.new_loop(sid)
            self.assertEqual(len(loop._context), 2)
            self.assertEqual(loop._context[0].role, "user")
            self.assertEqual(loop._context[0].content, "First message")
            self.assertEqual(loop._context[1].role, "assistant")
            self.assertEqual(loop._context[1].content, "First reply")
        finally:
            agent.close()


class TestGateway(unittest.IsolatedAsyncioTestCase):

    def _make_gateway(self, agent_defs=None):
        from hushclaw.gateway import Gateway
        config = _make_config(
            agents=agent_defs or [],
            shared_memory=False,
        )
        base_agent = _make_mock_agent("default")
        # Patch _build_agent_from_definition to avoid real Agent creation
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.return_value = _make_mock_agent("sub")
            gw = Gateway(config, base_agent)
        return gw, base_agent

    def test_list_agents_default(self):
        gw, _ = self._make_gateway()
        agents = gw.list_agents()
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]["name"], "default")

    def test_list_agents_with_definitions(self):
        defs = [
            AgentDefinition(name="researcher", description="Research agent"),
            AgentDefinition(name="writer"),
        ]
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.side_effect = [_make_mock_agent("researcher"), _make_mock_agent("writer")]
            from hushclaw.gateway import Gateway
            config = _make_config(agents=defs, shared_memory=False)
            gw = Gateway(config, _make_mock_agent("default"))
        agents = gw.list_agents()
        names = [a["name"] for a in agents]
        self.assertIn("default", names)
        self.assertIn("researcher", names)
        self.assertIn("writer", names)

    async def test_execute_routes_to_default(self):
        gw, _ = self._make_gateway()
        result = await gw.execute("default", "hello")
        self.assertIsInstance(result, str)

    async def test_execute_unknown_agent_falls_back_to_default(self):
        gw, _ = self._make_gateway()
        result = await gw.execute("nonexistent", "hello")
        self.assertIsInstance(result, str)

    async def test_execute_without_session_uses_stable_implicit_session(self):
        gw, base_agent = self._make_gateway()
        default_pool = gw.get_pool("default")
        await gw.execute("default", "first")
        await gw.execute("default", "second")
        self.assertEqual(base_agent.new_loop.call_count, 1)
        self.assertIn("auto_default", default_pool._loops)

    async def test_broadcast_returns_dict(self):
        gw, _ = self._make_gateway()
        results = await gw.broadcast(["default"], "ping")
        self.assertIn("default", results)
        self.assertIsInstance(results["default"], str)

    async def test_broadcast_multiple_agents_returns_all(self):
        gw, _ = self._make_gateway()
        with patch.object(gw, "execute", new=AsyncMock(side_effect=["out-a", "out-b"])) as mock_execute:
            results = await gw.broadcast(["a1", "a2"], "ping")
        self.assertEqual(results, {"a1": "out-a", "a2": "out-b"})
        self.assertEqual(mock_execute.await_count, 2)

    async def test_event_stream_yields_done(self):
        gw, _ = self._make_gateway()
        events = []
        async for ev in gw.event_stream("default", "hi"):
            events.append(ev)
        self.assertTrue(any(e["type"] == "done" for e in events))

    def test_create_agent_at_runtime(self):
        gw, _ = self._make_gateway()
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.return_value = _make_mock_agent("specialist")
            gw.create_agent("specialist", description="A specialist agent")
        names = [a["name"] for a in gw.list_agents()]
        self.assertIn("specialist", names)

    def test_create_agent_duplicate_raises(self):
        gw, _ = self._make_gateway()
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.return_value = _make_mock_agent("dup")
            gw.create_agent("dup")
        with self.assertRaises(ValueError):
            gw.create_agent("dup")

    def test_create_agent_default_name_raises(self):
        gw, _ = self._make_gateway()
        with self.assertRaises(ValueError):
            gw.create_agent("default")

    def test_create_agent_invalid_name_raises(self):
        gw, _ = self._make_gateway()
        with self.assertRaises(ValueError):
            gw.create_agent("agent with space")
        with self.assertRaises(ValueError):
            gw.create_agent("中文代理")

    async def test_spawn_agent_tool(self):
        from hushclaw.tools.builtins.agent_tools import spawn_agent
        mock_gw = MagicMock()
        mock_gw.create_agent = MagicMock()
        mock_gw.execute = AsyncMock(return_value="specialist response")
        result = await spawn_agent(
            agent_name="specialist",
            task="What is 2+2?",
            description="Math specialist",
            _gateway=mock_gw,
        )
        mock_gw.create_agent.assert_called_once_with(
            name="specialist",
            description="Math specialist",
            model="",
            system_prompt="",
            instructions="",
            role="specialist",
            team="",
            reports_to="",
            capabilities=[],
        )
        mock_gw.execute.assert_called_once_with("specialist", "What is 2+2?")
        self.assertFalse(result.is_error)
        self.assertEqual(result.content, "specialist response")

    def test_update_agent_tool_supports_explicit_clear_flags(self):
        from hushclaw.tools.builtins.agent_tools import update_agent
        mock_gw = MagicMock()
        mock_gw.update_agent = MagicMock()
        result = update_agent(
            agent_name="analyst",
            clear_team=True,
            clear_reports_to=True,
            clear_capabilities=True,
            _gateway=mock_gw,
        )
        self.assertFalse(result.is_error)
        mock_gw.update_agent.assert_called_once_with(
            name="analyst",
            team="",
            reports_to="",
            capabilities=[],
        )

    def test_hierarchy_fields_roundtrip_runtime_agent(self):
        gw, _ = self._make_gateway()
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.return_value = _make_mock_agent("cmdr")
            gw.create_agent(
                "cmdr",
                description="Commander",
                role="commander",
                team="market",
                capabilities=["dispatch", "synthesis"],
            )
        detail = gw.get_agent_def("cmdr")
        self.assertEqual(detail["role"], "commander")
        self.assertEqual(detail["team"], "market")
        self.assertEqual(detail["reports_to"], "")
        self.assertEqual(detail["capabilities"], ["dispatch", "synthesis"])

    def test_create_agent_role_alias_normalization(self):
        gw, _ = self._make_gateway()
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.return_value = _make_mock_agent("opslead")
            gw.create_agent("opslead", role="manager")
        detail = gw.get_agent_def("opslead")
        self.assertEqual(detail["role"], "commander")

    def test_create_agent_reports_to_missing_raises(self):
        # Forward references to unknown agents are intentionally allowed.
        # Self-references (reports_to == name) must still raise ValueError.
        gw, _ = self._make_gateway()
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.return_value = _make_mock_agent("child")
            with self.assertRaises(ValueError):
                gw.create_agent("child", reports_to="child")

    def test_hierarchy_cycle_detection_raises(self):
        gw, _ = self._make_gateway()
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.side_effect = [_make_mock_agent("a"), _make_mock_agent("b")]
            gw.create_agent("a")
            gw.create_agent("b", reports_to="a")
            with self.assertRaises(ValueError):
                gw.update_agent("a", reports_to="b")

    async def test_execute_hierarchical_parallel(self):
        gw, _ = self._make_gateway()
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.side_effect = [
                _make_mock_agent("commander"),
                _make_mock_agent("child1"),
                _make_mock_agent("child2"),
            ]
            gw.create_agent("commander", role="commander")
            gw.create_agent("child1", reports_to="commander")
            gw.create_agent("child2", reports_to="commander")
        out = await gw.execute_hierarchical("commander", "analyze")
        self.assertIn("Hierarchical Dispatch", out)
        self.assertIn("child1", out)
        self.assertIn("child2", out)

    async def test_execute_hierarchical_sequential(self):
        gw, _ = self._make_gateway()
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.side_effect = [_make_mock_agent("commander"), _make_mock_agent("child1")]
            gw.create_agent("commander", role="commander")
            gw.create_agent("child1", reports_to="commander")
        out = await gw.execute_hierarchical("commander", "analyze", mode="sequential")
        self.assertIn("Mode: sequential", out)
        self.assertIn("Final Synthesis", out)


class TestConfigParsing(unittest.TestCase):

    def test_agent_definition_defaults(self):
        defn = AgentDefinition(name="test")
        self.assertEqual(defn.model, "")
        self.assertEqual(defn.system_prompt, "")
        self.assertEqual(defn.tools, [])
        self.assertEqual(defn.role, "specialist")
        self.assertEqual(defn.team, "")
        self.assertEqual(defn.reports_to, "")
        self.assertEqual(defn.capabilities, [])

    def test_gateway_config_defaults(self):
        gc = GatewayConfig()
        self.assertTrue(gc.shared_memory)
        self.assertEqual(gc.max_concurrent_per_agent, 10)
        self.assertEqual(gc.agents, [])

    def test_server_config_defaults(self):
        sc = ServerConfig()
        self.assertEqual(sc.host, "127.0.0.1")
        self.assertEqual(sc.port, 8765)
        self.assertEqual(sc.api_key, "")

    def test_config_has_gateway_and_server(self):
        c = Config()
        self.assertIsInstance(c.gateway, GatewayConfig)
        self.assertIsInstance(c.server, ServerConfig)

    def test_loader_parses_gateway(self):
        from hushclaw.config.loader import _make_gateway_config
        data = {
            "shared_memory": False,
            "max_concurrent_per_agent": 5,
            "agents": [
                {"name": "researcher", "description": "Web researcher", "model": "claude-opus-4-6"},
                {"name": "writer", "tools": ["remember", "recall"], "role": "commander", "team": "ops", "capabilities": ["dispatch"]},
            ],
        }
        gc = _make_gateway_config(data)
        self.assertFalse(gc.shared_memory)
        self.assertEqual(gc.max_concurrent_per_agent, 5)
        self.assertEqual(len(gc.agents), 2)
        self.assertEqual(gc.agents[0].name, "researcher")
        self.assertEqual(gc.agents[0].model, "claude-opus-4-6")
        self.assertEqual(gc.agents[1].tools, ["remember", "recall"])
        self.assertEqual(gc.agents[1].role, "commander")
        self.assertEqual(gc.agents[1].team, "ops")
        self.assertEqual(gc.agents[1].capabilities, ["dispatch"])

    def test_build_agent_from_definition_model_override(self):
        from hushclaw.gateway import _build_agent_from_definition
        config = _make_config()
        defn = AgentDefinition(name="fast", model="claude-haiku-4-5-20251001")

        # Agent is imported locally inside _build_agent_from_definition
        with patch("hushclaw.agent.Agent") as MockAgent:
            mock_instance = MagicMock()
            MockAgent.return_value = mock_instance
            _build_agent_from_definition(defn, config, shared_memory=None)
            call_kwargs = MockAgent.call_args[1]
            self.assertEqual(call_kwargs["config"].agent.model, "claude-haiku-4-5-20251001")

    def test_build_agent_from_definition_tools_override(self):
        from hushclaw.gateway import _build_agent_from_definition
        config = _make_config()
        defn = AgentDefinition(name="researcher", tools=["recall", "fetch_url"])

        with patch("hushclaw.agent.Agent") as MockAgent:
            mock_instance = MagicMock()
            MockAgent.return_value = mock_instance
            _build_agent_from_definition(defn, config, shared_memory=None)
            call_kwargs = MockAgent.call_args[1]
            self.assertEqual(call_kwargs["config"].tools.enabled, ["recall", "fetch_url"])


if __name__ == "__main__":
    unittest.main()
