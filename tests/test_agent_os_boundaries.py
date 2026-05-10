from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from hushclaw.extensions import ExtensionRegistry
from hushclaw.memory import MemoryStore, SQLiteMemoryPort
from hushclaw.os_api import AgentOSService
from hushclaw.runtime import RuntimePrincipal, current_principal, principal_context
from hushclaw.runtime.policy import PolicyGate
from hushclaw.runtime.tool_runtime import ToolCall, ToolRuntime
from hushclaw.tools.base import ToolResult, tool
from hushclaw.tools.executor import ToolExecutor
from hushclaw.tools.registry import ToolRegistry
from hushclaw.tools.runtime_context import ToolRuntimeContext


def test_principal_context_defaults_and_overrides():
    assert current_principal().principal_id == "local-user"
    principal = RuntimePrincipal(principal_id="u-1", org_id="org-1", roles=("member",), source_channel="api")
    with principal_context(principal):
        assert current_principal().principal_id == "u-1"
        assert current_principal().source_channel == "api"
    assert current_principal().principal_id == "local-user"


def test_sqlite_memory_port_remember_recall_and_promote():
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(Path(d), embed_provider="local")
        port = SQLiteMemoryPort(store)
        note_id = port.remember(
            "Agent OS memory boundary",
            scope="workspace:demo",
            metadata={"title": "Boundary", "tags": ["architecture"]},
        )
        assert note_id
        recalled = port.recall("Agent OS", scopes=["workspace:demo"], limit=3)
        assert "Agent OS" in recalled
        assert port.promote(note_id, "global")


def test_tool_runtime_writes_audit_events_with_principal():
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(Path(d), embed_provider="local")

        @tool(name="hello_tool", description="Say hello")
        def hello_tool() -> ToolResult:
            return ToolResult.ok("hello")

        reg = ToolRegistry()
        reg.register(hello_tool)
        runtime = ToolRuntime(
            executor=ToolExecutor(reg, timeout=5),
            policy_gate=PolicyGate(),
            runtime_context=ToolRuntimeContext(
                session_id="s-audit",
                memory=store,
                principal=RuntimePrincipal(principal_id="u-audit", source_channel="api"),
            ),
        )

        record = asyncio.run(runtime.execute(ToolCall(name="hello_tool", arguments={}, entrypoint="test")))

        assert not record.result.is_error
        events = store.events.session_events("s-audit")
        audit_events = [e for e in events if e["type"].startswith("audit:")]
        assert [e["type"] for e in audit_events] == ["audit:tool_call", "audit:tool_result"]
        assert audit_events[0]["payload"]["principal"]["principal_id"] == "u-audit"


def test_extension_registry_lists_agents_from_gateway():
    gateway = SimpleNamespace(
        base_agent=SimpleNamespace(_skill_registry=None, config=SimpleNamespace(app_connectors=None)),
        list_agents=lambda: [
            {"name": "default", "description": "Default agent", "capabilities": ["general"]},
        ],
    )
    items = ExtensionRegistry(gateway).list()
    agent_items = [item for item in items if item["manifest"]["kind"] == "agent"]
    assert agent_items
    assert agent_items[0]["manifest"]["id"] == "agent:default"


def test_agent_os_service_sessions_todos_and_scheduled_tasks():
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(Path(d), embed_provider="local")
        store.save_turn("s-1", "user", "hello world", workspace="Demo")
        gateway = SimpleNamespace(
            memory=store,
            base_agent=SimpleNamespace(memory=store),
            clear_all_cached_loops=lambda: None,
        )
        service = AgentOSService(gateway)

        sessions, has_more = service.list_sessions(limit=10, workspace="Demo")
        assert not has_more
        assert sessions[0]["session_id"] == "s-1"
        history = service.session_history("s-1")
        assert history["turns"]

        todo = service.create_todo({"title": "Ship OS seam", "priority": 1, "tags": ["os"]})
        assert todo["title"] == "Ship OS seam"
        assert service.list_todos(status="pending")
        updated = service.update_todo(todo["todo_id"], {"status": "done"})
        assert updated["status"] == "done"
        assert service.delete_todo(todo["todo_id"])

        task = service.create_scheduled_task({"cron": "* * * * *", "prompt": "brief", "title": "Briefing"})
        assert task["title"] == "Briefing"
        assert service.toggle_scheduled_task(task["id"], False)
        assert service.delete_scheduled_task(task["id"])


def test_agent_os_service_memory_and_profile_boundaries():
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(Path(d), embed_provider="local")
        note_id = store.remember("Visible memory", title="Visible", tags=["demo"], scope="global")
        gateway = SimpleNamespace(
            memory=store,
            base_agent=SimpleNamespace(
                memory=store,
                search=lambda query, limit=5, include_kinds=None: store.search(query, limit=limit, include_kinds=include_kinds),
                list_memories=lambda **kwargs: store.list_recent_notes(**kwargs),
                forget=lambda nid: store.delete_note(nid),
            ),
        )
        service = AgentOSService(gateway)

        items, has_more = service.list_memories(limit=5)
        assert not has_more
        assert items[0]["note_id"] == note_id
        assert service.delete_memory(note_id)
