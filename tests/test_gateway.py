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

    async def test_same_thread_id_reuses_same_loop(self):
        """Same thread_id should reuse the same cached loop."""
        from hushclaw.gateway import AgentPool
        agent = _make_mock_agent("test")
        agent.memory.conn.execute.return_value.fetchone.return_value = {"session_id": "s-thread"}
        pool = AgentPool(agent, "test", max_concurrent=5)

        await pool.execute("msg1", thread_id="th-aaa")
        await pool.execute("msg2", thread_id="th-aaa")

        self.assertEqual(agent.new_loop.call_count, 1)

    async def test_different_thread_ids_same_session_create_different_loops(self):
        """Different thread_ids should not share a cached loop."""
        from hushclaw.gateway import AgentPool
        agent = _make_mock_agent("test")

        def _fetchone():
            return {"session_id": "s-shared"}

        agent.memory.conn.execute.return_value.fetchone.side_effect = [_fetchone(), _fetchone()]
        pool = AgentPool(agent, "test", max_concurrent=5)

        await pool.execute("msg1", thread_id="th-a")
        await pool.execute("msg2", thread_id="th-b")

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

    async def test_event_stream_records_run_started_with_session_log_boundary(self):
        from hushclaw.gateway import AgentPool
        from hushclaw.memory.store import MemoryStore

        with tempfile.TemporaryDirectory() as td:
            agent = _make_mock_agent("evtest")
            agent.memory = MemoryStore(Path(td))
            agent.new_loop.return_value.session_id = "s-ev2"
            agent.new_loop.return_value.memory = agent.memory
            pool = AgentPool(agent, "evtest", max_concurrent=5)

            events = []
            async for ev in pool.event_stream("hello", session_id="s-ev2"):
                events.append(ev)

            self.assertTrue(any(e["type"] == "done" for e in events))
            stored = agent.memory.session_log.events_by_session("s-ev2")
            started = [e for e in stored if e["type"] == "run_started"]
            self.assertEqual(len(started), 1)
            self.assertEqual(started[0]["payload"]["agent"], "evtest")
            self.assertEqual(started[0]["payload"]["trigger"], "user")


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
        self.assertEqual(len(default_pool._loops), 1)
        self.assertTrue(next(iter(default_pool._loops)).startswith("thread:"))

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

    async def test_event_stream_passes_workspace_name_to_pool(self):
        gw, _ = self._make_gateway()
        pool = gw.get_pool("default")

        async def _fake_stream(*args, **kwargs):
            yield {"type": "done", "text": "", "input_tokens": 0, "output_tokens": 0}

        pool.event_stream = MagicMock(side_effect=_fake_stream)

        events = []
        async for ev in gw.event_stream("default", "hi", workspace="Workflows"):
            events.append(ev)

        self.assertTrue(any(e["type"] == "done" for e in events))
        self.assertEqual(pool.event_stream.call_args.kwargs.get("workspace_name"), "Workflows")

    async def test_execute_child_run_persists_run_tree_and_updates_session_runtime(self):
        from hushclaw.gateway import Gateway
        from hushclaw.memory.store import MemoryStore
        from hushclaw.server.session import _SessionEntry

        with tempfile.TemporaryDirectory() as td:
            memory = MemoryStore(Path(td))
            base_agent = _make_mock_agent("default")
            base_agent.memory = memory
            loop = MagicMock()
            loop.session_id = "s-child"
            loop.memory = memory
            loop.executor = MagicMock()
            loop.pipeline_run_id = ""

            async def _event_gen(text, **kwargs):
                yield {"type": "round_info", "round": 1, "max_rounds": 2}
                yield {"type": "tool_call", "tool": "delegate_to_agent", "call_id": "call-child"}
                yield {"type": "done", "text": "child result", "input_tokens": 1, "output_tokens": 1}

            loop.event_stream = _event_gen
            base_agent.new_loop = MagicMock(return_value=loop)

            with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
                mock_build.return_value = _make_mock_agent("sub")
                gw = Gateway(_make_config(shared_memory=False), base_agent)

            session_id = "s-child"
            parent_thread_id = memory.get_or_create_thread(session_id, agent_name="default")
            parent_run_id = memory.create_run(parent_thread_id, session_id)
            entry = _SessionEntry(session_id=session_id, memory=memory)

            result = await gw.execute(
                "default",
                "hello child",
                session_id=session_id,
                parent_thread_id=parent_thread_id,
                parent_run_id=parent_run_id,
                trigger_type="sub_agent",
                run_kind="child",
                visibility="background",
                session_entry=entry,
            )

            self.assertEqual(result, "child result")
            meta = entry.runtime_meta()
            self.assertEqual(len(meta["child_runs"]), 1)
            child = meta["child_runs"][0]
            self.assertEqual(child["parent_run_id"], parent_run_id)
            self.assertEqual(child["run_kind"], "child")
            self.assertEqual(child["visibility"], "background")
            self.assertEqual(child["state"], "completed")
            rows = memory.conn.execute(
                "SELECT parent_run_id, trigger_type, run_kind, visibility FROM runs WHERE run_id=?",
                (child["run_id"],),
            ).fetchone()
            self.assertIsNotNone(rows)
            self.assertEqual(rows["parent_run_id"], parent_run_id)
            self.assertEqual(rows["trigger_type"], "sub_agent")
            self.assertEqual(rows["run_kind"], "child")
            self.assertEqual(rows["visibility"], "background")

    async def test_execute_child_run_emits_child_run_state_events_to_subscriber(self):
        from hushclaw.gateway import Gateway
        from hushclaw.server.session import _SessionEntry

        class _Subscriber:
            def __init__(self):
                self.events = []

            async def send(self, raw):
                self.events.append(raw)

        base_agent = _make_mock_agent("default")
        loop = MagicMock()
        loop.session_id = "s-child-events"
        loop.memory = base_agent.memory
        loop.executor = MagicMock()
        loop.pipeline_run_id = ""

        async def _event_gen(text, **kwargs):
            yield {"type": "round_info", "round": 1, "max_rounds": 1}
            yield {"type": "done", "text": "ok", "input_tokens": 1, "output_tokens": 1}

        loop.event_stream = _event_gen
        base_agent.new_loop = MagicMock(return_value=loop)

        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.return_value = _make_mock_agent("sub")
            gw = Gateway(_make_config(shared_memory=False), base_agent)

        entry = _SessionEntry(session_id="s-child-events", memory=None)
        entry.subscriber = _Subscriber()

        await gw.execute(
            "default",
            "hello child",
            session_id="s-child-events",
            parent_thread_id="th-parent",
            parent_run_id="run-parent",
            trigger_type="sub_agent",
            run_kind="child",
            visibility="background",
            session_entry=entry,
        )

        joined = "\n".join(entry.subscriber.events)
        self.assertIn('"type": "child_run_state_changed"', joined)
        self.assertIn('"state": "completed"', joined)

    def test_create_agent_at_runtime(self):
        gw, _ = self._make_gateway()
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.return_value = _make_mock_agent("specialist")
            with patch.object(gw, "clear_all_cached_loops") as mock_clear:
                gw.create_agent("specialist", description="A specialist agent")
        mock_clear.assert_called_once()
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

    def test_dynamic_agents_path_uses_workspace_dir(self):
        from hushclaw.gateway import Gateway

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            workspace = root / ".hushclaw"
            workspace.mkdir()
            config = _make_config(shared_memory=False)
            config = dataclasses.replace(
                config,
                memory=dataclasses.replace(config.memory, data_dir=root / "data"),
                agent=dataclasses.replace(config.agent, workspace_dir=workspace),
            )
            with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
                mock_build.return_value = _make_mock_agent("sub")
                gw = Gateway(config, _make_mock_agent("default"))

            self.assertEqual(gw._dynamic_agents_path(), workspace / "dynamic_agents.json")

    def test_load_dynamic_agents_prefers_workspace_file(self):
        from hushclaw.gateway import Gateway

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            workspace = root / ".hushclaw"
            workspace.mkdir()
            (workspace / "dynamic_agents.json").write_text(
                '[{"name":"workspace-agent","description":"ws"}]',
                encoding="utf-8",
            )
            config = _make_config(shared_memory=False)
            config = dataclasses.replace(
                config,
                memory=dataclasses.replace(config.memory, data_dir=root / "data"),
                agent=dataclasses.replace(config.agent, workspace_dir=workspace),
            )
            with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
                mock_build.return_value = _make_mock_agent("workspace-agent")
                gw = Gateway(config, _make_mock_agent("default"))

            names = [a["name"] for a in gw.list_agents()]
            self.assertIn("workspace-agent", names)

    def test_load_dynamic_agents_falls_back_to_legacy_data_dir_file(self):
        from hushclaw.gateway import Gateway

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            workspace = root / ".hushclaw"
            workspace.mkdir()
            data_dir = root / "data"
            data_dir.mkdir()
            (data_dir / "dynamic_agents.json").write_text(
                '[{"name":"legacy-agent","description":"legacy"}]',
                encoding="utf-8",
            )
            config = _make_config(shared_memory=False)
            config = dataclasses.replace(
                config,
                memory=dataclasses.replace(config.memory, data_dir=data_dir),
                agent=dataclasses.replace(config.agent, workspace_dir=workspace),
            )
            with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
                mock_build.return_value = _make_mock_agent("legacy-agent")
                gw = Gateway(config, _make_mock_agent("default"))

            names = [a["name"] for a in gw.list_agents()]
            self.assertIn("legacy-agent", names)

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
            system_prompt="",
            instructions="",
            routing_tags=[],
            tools=[],
        )
        mock_gw.execute.assert_called_once_with("specialist", "What is 2+2?")
        self.assertFalse(result.is_error)
        self.assertEqual(result.content, "specialist response")

    async def test_delegate_tool_propagates_parent_run_context(self):
        from hushclaw.tools.builtins.agent_tools import delegate_to_agent

        mock_gw = MagicMock()
        mock_gw.execute = AsyncMock(return_value="delegated")
        entry = object()

        result = await delegate_to_agent(
            agent_name="researcher",
            task="look into this",
            _gateway=mock_gw,
            _session_id="s-123",
            _current_thread_id="th-parent",
            _current_run_id="run-parent",
            _current_session_entry=entry,
        )

        self.assertFalse(result.is_error)
        mock_gw.execute.assert_called_once_with(
            "researcher",
            "look into this",
            session_id="s-123",
            parent_thread_id="th-parent",
            parent_run_id="run-parent",
            session_entry=entry,
            trigger_type="sub_agent",
            run_kind="child",
            visibility="foreground",
        )

    def test_update_agent_tool_supports_explicit_clear_flags(self):
        from hushclaw.tools.builtins.agent_tools import update_agent
        mock_gw = MagicMock()
        mock_gw.update_agent = MagicMock()
        result = update_agent(
            agent_name="analyst",
            clear_routing_tags=True,
            _gateway=mock_gw,
        )
        self.assertFalse(result.is_error)
        mock_gw.update_agent.assert_called_once_with(
            name="analyst",
            routing_tags=[],
        )

    def test_routing_tags_roundtrip_runtime_agent(self):
        gw, _ = self._make_gateway()
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.return_value = _make_mock_agent("analyst")
            gw.create_agent(
                "analyst",
                description="Analyst",
                routing_tags=["research", "synthesis"],
            )
        detail = gw.get_agent_def("analyst")
        self.assertEqual(detail["routing_tags"], ["research", "synthesis"])

    def test_delete_agent_clears_cached_loops(self):
        gw, _ = self._make_gateway()
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.return_value = _make_mock_agent("specialist")
            gw.create_agent("specialist", description="A specialist agent")
        with patch.object(gw, "clear_all_cached_loops") as mock_clear:
            gw.delete_agent("specialist")
        mock_clear.assert_called_once()

    def test_update_agent_clears_cached_loops(self):
        gw, _ = self._make_gateway()
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.return_value = _make_mock_agent("specialist")
            gw.create_agent("specialist", description="A specialist agent")
        with patch("hushclaw.gateway._build_agent_from_definition") as mock_build:
            mock_build.return_value = _make_mock_agent("specialist")
            with patch.object(gw, "clear_all_cached_loops") as mock_clear:
                gw.update_agent("specialist", description="Updated specialist")
        mock_clear.assert_called_once()


class TestConfigParsing(unittest.TestCase):

    def test_agent_definition_defaults(self):
        defn = AgentDefinition(name="test")
        self.assertEqual(defn.model, "")
        self.assertEqual(defn.system_prompt, "")
        self.assertEqual(defn.tools, [])
        self.assertEqual(defn.routing_tags, [])

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
                {"name": "writer", "tools": ["remember", "recall"], "routing_tags": ["writing"]},
            ],
        }
        gc = _make_gateway_config(data)
        self.assertFalse(gc.shared_memory)
        self.assertEqual(gc.max_concurrent_per_agent, 5)
        self.assertEqual(len(gc.agents), 2)
        self.assertEqual(gc.agents[0].name, "researcher")
        self.assertEqual(gc.agents[0].model, "claude-opus-4-6")
        self.assertEqual(gc.agents[1].tools, ["remember", "recall"])
        self.assertEqual(gc.agents[1].routing_tags, ["writing"])

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


class TestAgentPoolLifecycle(unittest.IsolatedAsyncioTestCase):
    """Phase 11: lifecycle correctness for _drop_loop and clear_cached_loops."""

    def _make_pool(self):
        from hushclaw.gateway import AgentPool
        mock_agent = MagicMock()
        mock_loop = MagicMock()
        mock_loop.aclose = AsyncMock()
        mock_loop.session_id = "ses-pool"
        mock_loop.memory = MagicMock()
        mock_loop.executor = MagicMock()
        mock_loop.pipeline_run_id = ""
        mock_agent.new_loop = MagicMock(return_value=mock_loop)
        pool = AgentPool(mock_agent, "test-agent")
        return pool, mock_loop

    async def test_clear_cached_loops_calls_aclose(self):
        """clear_cached_loops() must schedule aclose() for every cached loop."""
        pool, mock_loop = self._make_pool()
        pool._loops["ses-1"] = mock_loop
        pool._loop_last_used["ses-1"] = 0.0

        pool.clear_cached_loops()

        # Give the event loop one tick to run the created task.
        await asyncio.sleep(0)
        mock_loop.aclose.assert_awaited_once()
        self.assertEqual(pool._loops, {})

    def test_clear_cached_loops_without_running_loop_closes_sync(self):
        """Synchronous tools can invalidate loops without a running asyncio loop."""
        pool, mock_loop = self._make_pool()
        pool._loops["ses-1"] = mock_loop
        pool._loop_last_used["ses-1"] = 0.0

        pool.clear_cached_loops()

        mock_loop.aclose.assert_awaited_once()
        self.assertEqual(pool._loops, {})

    async def test_gc_stale_sessions_uses_drop_loop(self):
        """_gc_stale_sessions() schedules aclose() via _drop_loop for TTL-expired sessions."""
        pool, mock_loop = self._make_pool()
        pool._session_ttl = 1
        pool._loops["ses-stale"] = mock_loop
        pool._loop_last_used["ses-stale"] = 0.0  # far in the past

        pool._gc_stale_sessions()

        await asyncio.sleep(0)
        mock_loop.aclose.assert_awaited_once()
        self.assertNotIn("ses-stale", pool._loops)


class TestAgentAclose(unittest.IsolatedAsyncioTestCase):
    """Phase 11: Agent.aclose() awaits workers before closing memory."""

    async def test_aclose_stops_workers_before_memory(self):
        """aclose() must await worker.stop() before calling memory.close()."""
        from hushclaw.agent import Agent

        call_order = []

        mock_worker = MagicMock()
        async def fake_stop():
            call_order.append("worker_stop")
        mock_worker.stop = fake_stop

        mock_memory = MagicMock()
        def fake_mem_close():
            call_order.append("memory_close")
        mock_memory.close = fake_mem_close

        agent = Agent.__new__(Agent)
        agent._projection_worker = mock_worker
        agent._retention_executor = None
        agent.memory = mock_memory

        await agent.aclose()

        self.assertEqual(call_order, ["worker_stop", "memory_close"])


if __name__ == "__main__":
    unittest.main()
