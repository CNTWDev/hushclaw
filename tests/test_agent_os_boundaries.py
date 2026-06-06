from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from hushclaw.extensions import ExtensionRegistry
from hushclaw.domains import DomainManifestError, DomainRegistry
from hushclaw.distro import DistroRuntime
from hushclaw.distro.base import AgentProfile, DistroManifest, PolicyRuleSet
from hushclaw.enterprise import EnterpriseDirectory, EnterpriseDirectoryStore
from hushclaw.config.schema import Config, AgentConfig, ProviderConfig, MemoryConfig, ToolsConfig, LoggingConfig, ContextPolicyConfig, GatewayConfig, ServerConfig
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


def test_enterprise_directory_auth_hashes_password_and_creates_session():
    directory = EnterpriseDirectory()
    member = directory.upsert_member({
        "display_name": "Ada Lovelace",
        "email": "ada@example.com",
        "temporary_password": "temporary-secret",
    })

    credentials = directory.list_credentials()
    credential = next(item for item in credentials if item["member_id"] == member["id"])
    assert credential["login_id"] == "ada@example.com"
    assert "temporary-secret" not in repr(directory.snapshot().credentials)

    failure = directory.authenticate("ada@example.com", "wrong-password")
    assert not failure["ok"]

    success = directory.authenticate("ada@example.com", "temporary-secret")
    assert success["ok"]
    assert success["member"]["id"] == member["id"]
    assert "member" in success["roles"]

    session_id = success["session"]["session_id"]
    session_member = directory.member_for_session(session_id)
    assert session_member is not None
    assert session_member[0].id == member["id"]
    assert directory.logout(session_id)
    assert directory.member_for_session(session_id) is None


def test_enterprise_directory_store_backfills_bootstrap_credential_for_old_snapshots():
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(Path(d), embed_provider="local")
        directory = EnterpriseDirectory.default_snapshot()
        old_directory = EnterpriseDirectory.from_dict({
            "org": directory.org.to_dict(),
            "units": [item.to_dict() for item in directory.units],
            "positions": [item.to_dict() for item in directory.positions],
            "members": [item.to_dict() for item in directory.members],
            "roles": [item.to_dict() for item in directory.roles],
            "assignments": [item.to_dict() for item in directory.assignments],
            "teams": [item.to_dict() for item in directory.teams],
            "domain_access": [],
        })
        directory_store = EnterpriseDirectoryStore(store.conn)
        directory_store.save(old_directory)

        loaded = directory_store.load()
        result = loaded.authenticate("local@hushclaw.enterprise", "hushclaw-admin")
        assert result["ok"]
        assert result["member"]["id"] == "local-user"
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


def test_sqlite_memory_port_serializes_concurrent_recall_reads():
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(Path(d), embed_provider="local")
        port = SQLiteMemoryPort(store)
        port.remember(
            "Agent OS memory boundary and local SQLite stability",
            scope="global",
            metadata={"title": "SQLite Boundary"},
        )
        assert port.search("Agent OS", limit=3)

        async def _run_many() -> list[str]:
            return await asyncio.gather(*[
                asyncio.to_thread(port.recall, "Agent OS SQLite", limit=3)
                for _ in range(8)
            ])

        results = asyncio.run(_run_many())

        assert len(results) == 8
        assert all(isinstance(item, str) for item in results)


def test_sqlite_memory_port_allows_parallel_reads_during_serial_writes():
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(Path(d), embed_provider="local")
        port = SQLiteMemoryPort(store)
        port.remember(
            "Parallel recall should use readonly SQLite connections",
            scope="global",
            metadata={"title": "Parallel Recall"},
        )

        async def _read(index: int) -> str:
            return await asyncio.to_thread(port.recall, f"parallel recall {index}", limit=3)

        async def _write(index: int) -> str:
            return await asyncio.to_thread(
                port.remember,
                f"Writer note {index}",
                scope="global",
                metadata={"title": f"Writer {index}"},
            )

        async def _run_mixed() -> tuple[list[str], list[str]]:
            reads = [asyncio.create_task(_read(i)) for i in range(12)]
            writes = [asyncio.create_task(_write(i)) for i in range(4)]
            return await asyncio.gather(*reads), await asyncio.gather(*writes)

        read_results, note_ids = asyncio.run(_run_mixed())

        assert len(read_results) == 12
        assert len(note_ids) == 4
        assert all(note_id for note_id in note_ids)


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
        events = store.session_log.session_events("s-audit")
        audit_events = [e for e in events if e["type"].startswith("audit:")]
        assert [e["type"] for e in audit_events] == ["audit:tool_call", "audit:tool_result"]
        assert audit_events[0]["payload"]["principal"]["principal_id"] == "u-audit"


def test_extension_registry_lists_agents_from_gateway():
    gateway = SimpleNamespace(
        base_agent=SimpleNamespace(_skill_registry=None, config=SimpleNamespace(app_connectors=None)),
        list_agents=lambda: [
            {"name": "default", "description": "Default agent", "routing_tags": ["general"]},
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
        for idx in range(3):
            service.create_todo({"title": f"Paged todo {idx}"})
        page, todos_more = service.list_todos(limit=2, offset=0)
        assert len(page) == 2
        assert todos_more
        updated = service.update_todo(todo["todo_id"], {"status": "done"})
        assert updated["status"] == "done"
        assert service.delete_todo(todo["todo_id"])

        insight = service.create_insight({"text": "Taste is compression of judgment."})
        assert insight is not None
        memory_note_id = service.gateway.memory.remember(
            "Systems should preserve useful tension.",
            title="Useful tension",
            tags=["_auto_extract"],
            note_type="interest",
            memory_kind="user_model",
        )
        insights, insights_more = service.list_insights(limit=10)
        assert not insights_more
        insight_by_id = {item["note_id"]: item for item in insights}
        assert insight_by_id[insight["note_id"]]["source_type"] == "curated"
        assert "insight" in insight_by_id[insight["note_id"]]["tags"]
        assert memory_note_id not in insight_by_id
        suggested, _ = service.list_insights(limit=10, view="suggested")
        memory_insight = next(item for item in suggested if item["note_id"] == memory_note_id)
        assert memory_insight["source_type"] == "memory"
        assert memory_insight["note_type"] == "interest"
        assert "quality" in memory_insight
        assert service.delete_insight(insight["note_id"])

        task = service.create_scheduled_task({"cron": "* * * * *", "prompt": "brief", "title": "Briefing"})
        assert task["title"] == "Briefing"
        assert service.toggle_scheduled_task(task["id"], False)
        assert service.delete_scheduled_task(task["id"])

        work = service.create_work_task({"title": "Work item", "spec": "Do the work"})
        assert work["status"] == "queued"
        assert service.list_work_tasks()[0]["task_id"] == work["task_id"]
        run = service.claim_work_task(work["task_id"], worker_id="tester")
        assert run and run["status"] == "running"
        assert service.complete_work_task(run["run_id"], "done")
        assert service.list_work_tasks(status="done")[0]["task_id"] == work["task_id"]
        retried = service.retry_work_task(work["task_id"])
        assert retried and retried["status"] == "queued"


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


def test_distro_runtime_builds_personal_bundle_before_shell_use():
    with tempfile.TemporaryDirectory() as d:
        cfg = Config(
            agent=AgentConfig(model="llama3.2"),
            provider=ProviderConfig(name="ollama"),
            memory=MemoryConfig(data_dir=Path(d), embed_provider="local"),
            tools=ToolsConfig(enabled=["get_time"]),
            logging=LoggingConfig(),
            context=ContextPolicyConfig(),
            gateway=GatewayConfig(),
            server=ServerConfig(),
        )
        bundle = DistroRuntime("personal").build(config=cfg)
        try:
            assert bundle.os_api.runtime.manifest()["id"] == "personal"
            assert bundle.gateway.base_agent is bundle.agent
            profile = bundle.os_api.runtime.profile()
            assert profile["default_path"] == "/personal"
            assert profile["current_shell"] == "personal"
            assert profile["enabled_domains"] == []
            assert profile["interfaces"] == {
                "runtime": True,
                "agents": True,
                "tools": True,
                "sessions": True,
                "memory": True,
                "tasks": True,
                "audit": True,
                "extensions": True,
            }
            assert isinstance(bundle.os_api.agents.list(), list)
            assert isinstance(bundle.os_api.tools.list(), list)
            assert isinstance(bundle.os_api.extensions.list(), list)
            assert not hasattr(bundle.os_api, "enterprise_overview")
            assert not hasattr(bundle.os_api, "list_domains")
            assert not hasattr(bundle.os_api, "crm_records")
        finally:
            bundle.close()


def test_distro_runtime_rejects_removed_team_distro():
    try:
        DistroRuntime("team")
    except ValueError as exc:
        message = str(exc)
        assert "Unknown distro 'team'" in message
        assert "personal" in message
    else:
        raise AssertionError("Expected removed team distro to be rejected")


def test_distro_runtime_rejects_removed_enterprise_distro():
    try:
        DistroRuntime("enterprise")
    except ValueError as exc:
        message = str(exc)
        assert "Unknown distro 'enterprise'" in message
        assert "personal" in message
    else:
        raise AssertionError("Expected removed enterprise distro to be rejected")


def test_generic_domain_registry_has_no_enterprise_business_defaults():
    registry = DomainRegistry()
    assert registry.list() == []
    assert registry.manifest("crm") == {}


def test_domain_manifest_validation_blocks_invalid_runtime_contracts():
    from hushclaw.domains.base import DomainManifest, StaticDomainRuntime

    invalid = StaticDomainRuntime(DomainManifest(
        id="bad domain",
        name="",
        module_type="crm_specific_type",
        status="preview",
        datasets=({"name": "Missing id"},),
        workflows=("not-an-object",),
        tools=("",),
    ))

    try:
        DomainRegistry([invalid])
    except DomainManifestError as exc:
        message = str(exc)
        assert "id must be a non-empty stable identifier without spaces" in message
        assert "name is required" in message
        assert "module_type must be one of" in message
        assert "status must be one of" in message
        assert "datasets[0].id is required" in message
        assert "workflows[0] must be an object" in message
        assert "tools[0] must be a non-empty string" in message
    else:
        raise AssertionError("Expected invalid domain manifest to be rejected")


def test_domain_registry_rejects_duplicate_domain_ids():
    from hushclaw.domains.base import DomainManifest, StaticDomainRuntime

    first = StaticDomainRuntime(DomainManifest(id="crm", name="CRM"))
    second = StaticDomainRuntime(DomainManifest(id="crm", name="CRM Copy"))

    try:
        DomainRegistry([first, second])
    except DomainManifestError as exc:
        assert "duplicate domain id: crm" in str(exc)
    else:
        raise AssertionError("Expected duplicate domain id to be rejected")


def test_domain_registry_validation_report_remains_domain_agnostic():
    from hushclaw.domains.base import DomainManifest, StaticDomainRuntime

    registry = DomainRegistry([
        StaticDomainRuntime(DomainManifest(
            id="demo",
            name="Demo",
            datasets=({"id": "records"},),
            workflows=({"id": "review"},),
        )),
    ])

    assert registry.validation_report() == {
        "ok": True,
        "items": [{"domain_id": "demo", "ok": True, "errors": []}],
    }


def test_crm_domain_package_keeps_declarative_contract_outside_runtime():
    from hushclaw.solutions.enterprise.crm.package import CRM_AGENT_DEFINITIONS, CRM_MANIFEST
    from hushclaw.solutions.enterprise.crm.runtime import CRMDomainRuntime

    assert CRM_MANIFEST.validation_errors() == []
    assert set(CRM_MANIFEST.agents) == {item["name"] for item in CRM_AGENT_DEFINITIONS}
    assert CRMDomainRuntime().manifest() is CRM_MANIFEST


def test_domain_registry_blocks_missing_dependencies():
    from hushclaw.domains.base import DomainManifest, StaticDomainRuntime

    registry = DomainRegistry([
        StaticDomainRuntime(DomainManifest(
            id="crm",
            name="CRM",
            dependencies=("people_foundation",),
            status="available",
        )),
    ])

    deps = registry.dependency_status("crm")
    assert not deps["ok"]
    assert deps["missing"] == ["people_foundation"]
    result = registry.install("crm")
    assert not result["ok"]
    assert result["missing_dependencies"] == ["people_foundation"]


def test_distro_runtime_rejects_unregistered_storage_profile_before_agent_creation():
    class _TeamLikeDistro:
        def manifest(self):
            return DistroManifest(
                id="test_team_storage",
                name="Test Team",
                description="Unsupported storage profile",
                storage_profile="postgres",
                policy_profile="workspace_rbac",
            )

        def agent_profile(self):
            return AgentProfile()

        def policy_rules(self):
            return PolicyRuleSet()

        def runtime_principal(self, **kwargs):
            return RuntimePrincipal(principal_id="team-user")

        async def on_startup(self, os_api):
            pass

        async def on_shutdown(self):
            pass

    DistroRuntime.register(_TeamLikeDistro())
    try:
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(
                agent=AgentConfig(model="llama3.2"),
                provider=ProviderConfig(name="ollama"),
                memory=MemoryConfig(data_dir=Path(d), embed_provider="local"),
                tools=ToolsConfig(enabled=[]),
                logging=LoggingConfig(),
                context=ContextPolicyConfig(),
                gateway=GatewayConfig(),
                server=ServerConfig(),
            )
            try:
                DistroRuntime("test_team_storage").build(config=cfg)
            except ValueError as exc:
                assert "storage_profile='postgres'" in str(exc)
            else:
                raise AssertionError("Expected unsupported storage profile to fail")
    finally:
        DistroRuntime._registry.pop("test_team_storage", None)


def test_distro_policy_rules_reach_gateway_loop_tool_runtime():
    class _NoToolsDistro:
        def manifest(self):
            return DistroManifest(
                id="test_no_tools",
                name="No Tools",
                description="Blocks all tools",
                storage_profile="local_sqlite",
                policy_profile="deny_tools",
            )

        def agent_profile(self):
            return AgentProfile()

        def policy_rules(self):
            return PolicyRuleSet(can_call_tool=lambda tool_name, principal: False)

        def runtime_principal(self, **kwargs):
            return RuntimePrincipal(principal_id="blocked-user")

        async def on_startup(self, os_api):
            pass

        async def on_shutdown(self):
            pass

    DistroRuntime.register(_NoToolsDistro())
    try:
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(
                agent=AgentConfig(model="llama3.2"),
                provider=ProviderConfig(name="ollama"),
                memory=MemoryConfig(data_dir=Path(d), embed_provider="local"),
                tools=ToolsConfig(enabled=["get_time"]),
                logging=LoggingConfig(),
                context=ContextPolicyConfig(),
                gateway=GatewayConfig(),
                server=ServerConfig(),
            )
            bundle = DistroRuntime("test_no_tools").build(config=cfg)
            try:
                loop = bundle.gateway.get_pool("default")._get_or_create_loop("s-policy", None, bundle.gateway)
                td = loop.registry.get("get_time")
                decision = loop.tool_runtime.policy_gate.can_call_tool(
                    RuntimePrincipal(principal_id="blocked-user"),
                    td,
                    {},
                )
                assert not decision.allowed
                assert "blocked by distro policy" in decision.reason
            finally:
                bundle.close()
    finally:
        DistroRuntime._registry.pop("test_no_tools", None)
