from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from hushclaw.extensions import ExtensionRegistry
from hushclaw.domains import DomainRegistry
from hushclaw.distro import DistroRuntime
from hushclaw.distro.base import AgentProfile, DistroManifest, PolicyRuleSet
from hushclaw.config.schema import Config, AgentConfig, ProviderConfig, MemoryConfig, ToolsConfig, LoggingConfig, ContextPolicyConfig, GatewayConfig, ServerConfig
from hushclaw.memory import MemoryStore, SQLiteMemoryPort
from hushclaw.os_api import AgentOSService
from hushclaw.os_api import EnterpriseDistroRequired
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
            assert bundle.os_api.distro_manifest()["id"] == "personal"
            assert bundle.gateway.base_agent is bundle.agent
            profile = bundle.os_api.runtime_profile()
            assert profile["default_path"] == "/personal"
            assert profile["current_shell"] == "personal"
            assert profile["enabled_domains"] == []
            assert bundle.os_api.list_domains() == []
            try:
                bundle.os_api.enterprise_overview()
            except EnterpriseDistroRequired as exc:
                assert "enterprise distro required" in str(exc)
            else:
                raise AssertionError("Personal distro must not expose enterprise overview")
        finally:
            bundle.close()


def test_distro_runtime_rejects_removed_team_distro():
    try:
        DistroRuntime("team")
    except ValueError as exc:
        message = str(exc)
        assert "Unknown distro 'team'" in message
        assert "enterprise" in message
        assert "personal" in message
    else:
        raise AssertionError("Expected removed team distro to be rejected")


def test_distro_runtime_builds_enterprise_bundle_with_directory_and_domains():
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
        bundle = DistroRuntime("enterprise").build(config=cfg)
        try:
            manifest = bundle.os_api.distro_manifest()
            assert manifest["id"] == "enterprise"
            assert manifest["web_shell"] == "enterprise_workspace"
            assert manifest["admin_shell"] == "enterprise_admin"
            assert "domain_runtime" in manifest["capabilities"]

            profile = bundle.os_api.runtime_profile()
            assert profile["default_path"] == "/enterprise"
            shell_ids = {item["id"] for item in profile["available_shells"]}
            assert {"enterprise_workspace", "enterprise_admin"} <= shell_ids

            overview = bundle.os_api.enterprise_overview()
            assert overview["directory"]["counts"]["members"] == 1
            assert overview["directory"]["counts"]["positions"] == 1
            assert "Position & Reporting Graph" in overview["platform"]["foundation"]
            assert overview["domains"]["total"] >= 3

            foundation = bundle.os_api.foundation_catalog()
            assert {item["id"] for item in foundation} >= {
                "organization_directory",
                "identity_access",
                "policy_audit",
                "module_catalog",
            }

            unit = bundle.os_api.upsert_org_unit({"name": "Sales", "kind": "department"})
            assert unit["id"].startswith("unit-")
            position = bundle.os_api.upsert_position({"title": "Account Executive", "unit_id": unit["id"]})
            assert position["id"].startswith("pos-")
            member = bundle.os_api.upsert_member({
                "display_name": "Ada Sales",
                "email": "ada@example.com",
                "unit_id": unit["id"],
                "position_id": position["id"],
                "manager_id": "local-user",
            })
            assert member["id"].startswith("mem-")
            assert bundle.os_api.deactivate_member(member["id"])["ok"]
            assert any(item["id"] == member["id"] and item["status"] == "inactive" for item in bundle.os_api.list_members())
            role = bundle.os_api.upsert_role({"name": "CRM Admin", "permissions": ["crm.read", "crm.write"]})
            assignment = bundle.os_api.assign_role(member["id"], role["id"], scope="domain", scope_id="crm")
            assert assignment["scope"] == "domain"
            revoked = bundle.os_api.revoke_role(member["id"], role["id"], scope="domain", scope_id="crm")
            assert revoked["ok"]
            assert len(bundle.os_api.list_members()) == 2
            assert len(bundle.os_api.list_positions()) == 2

            settings = bundle.os_api.enterprise_settings()
            assert settings["module_install_policy"] == "owner_only"
            updated_settings = bundle.os_api.update_enterprise_settings({"audit_retention_days": 90})
            assert updated_settings["audit_retention_days"] == 90

            domains = bundle.os_api.list_domains()
            ids = {item["manifest"]["id"] for item in domains}
            assert {"people_foundation", "crm", "hr", "finance"} <= ids
            by_id = {item["manifest"]["id"]: item for item in domains}
            assert by_id["people_foundation"]["manifest"]["module_type"] == "foundation"
            assert by_id["people_foundation"]["status"]["enabled"]
            assert by_id["crm"]["manifest"]["dependencies"] == ["people_foundation"]
            assert by_id["crm"]["manifest"]["status"] == "available"
            assert by_id["hr"]["manifest"]["status"] == "planned"
            assert bundle.os_api.domain_manifest("crm")["entity_types"]
            assert bundle.os_api.domain_dependency_status("crm")["ok"]

            assert bundle.os_api.domain_config("crm")["config"] == {}
            updated_config = bundle.os_api.update_domain_config("crm", {"default_pipeline": "sales"})
            assert updated_config["config"]["default_pipeline"] == "sales"
            assert bundle.os_api.install_domain("crm")["ok"]
            assert bundle.os_api.enable_domain("crm")["ok"]
            assert bundle.os_api.domain_status("crm")["enabled"]
            domain_agents = {
                item["name"]: item for item in bundle.os_api.list_agents()
                if item.get("owner_type") == "domain"
            }
            assert {"crm.lead_qualifier", "crm.account_researcher", "crm.deal_coach"} <= set(domain_agents)
            assert domain_agents["crm.deal_coach"]["domain_id"] == "crm"
            assert not domain_agents["crm.deal_coach"]["editable"]
            assert bundle.os_api.disable_domain("crm")["ok"]
            assert not bundle.os_api.domain_status("crm")["enabled"]
            assert not [
                item for item in bundle.os_api.list_agents()
                if item.get("owner_type") == "domain" and item.get("domain_id") == "crm"
            ]
            assert not bundle.os_api.install_domain("hr")["ok"]

            ext_items = bundle.os_api.list_extensions()
            assert any(item["manifest"]["kind"] == "domain" and item["manifest"]["id"] == "domain:crm" for item in ext_items)
            audit_types = {item["payload"]["event_type"] for item in bundle.os_api.audit_events(limit=20)}
            assert {
                "directory.member.upserted",
                "directory.role.assigned",
                "directory.role.revoked",
                "settings.updated",
                "module.config.updated",
                "module.enabled",
            } <= audit_types
        finally:
            bundle.close()


def test_enterprise_runtime_factory_returns_isolated_state():
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        def _cfg(path: str):
            return Config(
                agent=AgentConfig(model="llama3.2"),
                provider=ProviderConfig(name="ollama"),
                memory=MemoryConfig(data_dir=Path(path), embed_provider="local"),
                tools=ToolsConfig(enabled=[]),
                logging=LoggingConfig(),
                context=ContextPolicyConfig(),
                gateway=GatewayConfig(),
                server=ServerConfig(),
            )

        first = DistroRuntime("enterprise").build(config=_cfg(d1))
        second = DistroRuntime("enterprise").build(config=_cfg(d2))
        try:
            first.os_api.upsert_member({"display_name": "Only First"})
            assert len(first.os_api.list_members()) == 2
            assert len(second.os_api.list_members()) == 1
            assert not second.os_api.domain_status("crm")["enabled"]
        finally:
            first.close()
            second.close()


def test_enterprise_directory_and_modules_persist_in_sqlite():
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
        first = DistroRuntime("enterprise").build(config=cfg)
        try:
            unit = first.os_api.upsert_org_unit({"name": "Revenue Ops"})
            member = first.os_api.upsert_member({"display_name": "Persisted Admin", "unit_id": unit["id"]})
            first.os_api.assign_role(member["id"], "domain-admin", scope="domain", scope_id="crm")
            first.os_api.install_domain("crm")
            first.os_api.enable_domain("crm")
            first.os_api.update_domain_config("crm", {"default_pipeline": "sales"})
            crm = first.os_api.domain_registry().get("crm")
            lead = crm.store.upsert("lead", {"name": "Persisted Lead", "owner_id": member["id"]})
            assert lead["name"] == "Persisted Lead"
            assert crm.store.next_actions(limit=5)
        finally:
            first.close()

        cfg2 = Config(
            agent=AgentConfig(model="llama3.2"),
            provider=ProviderConfig(name="ollama"),
            memory=MemoryConfig(data_dir=Path(d), embed_provider="local"),
            tools=ToolsConfig(enabled=[]),
            logging=LoggingConfig(),
            context=ContextPolicyConfig(),
            gateway=GatewayConfig(),
            server=ServerConfig(),
        )
        second = DistroRuntime("enterprise").build(config=cfg2)
        try:
            assert any(item["name"] == "Revenue Ops" for item in second.os_api.list_org_units())
            assert any(item["display_name"] == "Persisted Admin" for item in second.os_api.list_members())
            assert any(
                item["role_id"] == "domain-admin" and item["scope_id"] == "crm"
                for item in second.os_api.list_role_assignments()
            )
            assert second.os_api.domain_status("crm")["enabled"]
            assert second.os_api.domain_config("crm")["config"]["default_pipeline"] == "sales"
            assert any(item["name"] == "Persisted Lead" for item in second.os_api.crm_records("lead"))
            assert second.os_api.crm_next_actions()
        finally:
            second.close()


def test_generic_domain_registry_has_no_enterprise_business_defaults():
    registry = DomainRegistry()
    assert registry.list() == []
    assert registry.manifest("crm") == {}


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


def test_crm_domain_tools_are_registered_after_module_enabled():
    with tempfile.TemporaryDirectory() as d:
        def _cfg():
            return Config(
                agent=AgentConfig(model="llama3.2"),
                provider=ProviderConfig(name="ollama"),
                memory=MemoryConfig(data_dir=Path(d), embed_provider="local"),
                tools=ToolsConfig(enabled=[]),
                logging=LoggingConfig(),
                context=ContextPolicyConfig(),
                gateway=GatewayConfig(),
                server=ServerConfig(),
            )

        first = DistroRuntime("enterprise").build(config=_cfg())
        try:
            assert first.os_api.install_domain("crm")["ok"]
            assert first.os_api.enable_domain("crm")["ok"]
        finally:
            first.close()

        second = DistroRuntime("enterprise").build(config=_cfg())
        try:
            tool_names = {item["name"] for item in second.os_api.list_tools()}
            assert "crm.create_lead" in tool_names
            assert "crm.accept_next_action" in tool_names
            assert "crm.dismiss_next_action" in tool_names
            assert "crm.complete_next_action" in tool_names
            agent_names = {item["name"] for item in second.os_api.list_agents() if item.get("owner_type") == "domain"}
            assert {"crm.lead_qualifier", "crm.account_researcher", "crm.deal_coach"} <= agent_names
            crm = second.os_api.domain_registry().get("crm")
            lead = crm.store.upsert("lead", {"name": "AgentOS Lead", "source": "test"})
            assert lead["id"]
            assert second.os_api.crm_events(entity_type="lead", entity_id=lead["id"])
            assert any(
                event["event_type"] == "agent.next_action.suggested"
                for event in second.os_api.crm_events(entity_type="lead", entity_id=lead["id"])
            )
            result = crm.store.suggest_next_action("lead", lead["id"])
            assert "suggestion" in result
            next_actions = second.os_api.crm_next_actions()
            assert next_actions
            assert next_actions[0]["state_type"] == "next_action"
            assert next_actions[0]["status"] == "suggested"
            accepted = second.os_api.crm_update_next_action_status(next_actions[0]["state_id"], "accepted")
            assert accepted["ok"]
            assert accepted["item"]["status"] == "accepted"
            assert not any(item["state_id"] == next_actions[0]["state_id"] for item in second.os_api.crm_next_actions())
            rules = second.distro.policy_rules()
            assert rules.can_call_tool("crm.search_records", RuntimePrincipal(principal_id="crm.deal_coach", roles=("member",)))
            assert not rules.can_call_tool("crm.search_records", RuntimePrincipal(principal_id="plain-member", roles=("member",)))
        finally:
            second.close()


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
