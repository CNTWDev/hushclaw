"""Agent OS service facade.

Product shells should move toward this boundary instead of importing kernel
objects directly. The facade is intentionally thin for v1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hushclaw.extensions import ExtensionRegistry
from hushclaw.memory.kinds import SYSTEM_MEMORY_TAGS, USER_VISIBLE_MEMORY_KINDS
from hushclaw.memory.ports import SQLiteMemoryPort
from hushclaw.memory.taxonomy import (
    classify_belief_model,
    classify_note,
    classify_profile_fact,
    classify_reflection,
    context_taxonomy,
)
from hushclaw.runtime.audit import AuditEvent, append_audit_event
from hushclaw.runtime.principal import RuntimePrincipal, current_principal
from hushclaw.tools.base import to_api_schema
from hushclaw.util.ids import make_id


class EnterpriseDistroRequired(RuntimeError):
    """Raised when enterprise-only AgentOS APIs are called outside Enterprise."""


@dataclass(slots=True)
class AgentOSService:
    gateway: Any
    distro: Any = None  # DistroAdapter | None — injected by DistroRuntime.assemble()
    extra_routes: dict = field(default_factory=dict, init=False)  # prefix → async HTTP handler

    @property
    def principal(self) -> RuntimePrincipal:
        return current_principal()

    def distro_manifest(self) -> dict:
        if self.distro is not None:
            return self.distro.manifest().to_dict()
        return {}

    def web_shell_registry(self) -> "WebShellRegistry":
        from hushclaw.web_shells import WebShellRegistry
        return WebShellRegistry(self.distro)

    def is_enterprise(self) -> bool:
        return self.distro_manifest().get("id") == "enterprise"

    def require_enterprise(self) -> None:
        if not self.is_enterprise():
            raise EnterpriseDistroRequired("enterprise distro required")

    def runtime_profile(self) -> dict:
        registry = self.web_shell_registry()
        return {
            "distro": self.distro_manifest(),
            "available_shells": registry.list_available(),
            "current_shell": registry.default_shell_id(),
            "default_path": registry.default_path(),
            "enabled_domains": self.enabled_domains(),
            "principal": self.principal.to_dict(),
            "capabilities": self.distro_manifest().get("capabilities", []),
        }

    def register_http_handler(self, prefix: str, handler) -> None:
        """Register an async HTTP handler for paths starting with *prefix* (API port)."""
        self.extra_routes[prefix] = handler

    def list_agents(self) -> list[dict]:
        agents = self.gateway.list_agents()
        if not self.is_enterprise() or self.principal.is_owner:
            return agents
        return [
            agent for agent in agents
            if agent.get("owner_type") != "domain"
            or self.can_use_domain(str(agent.get("domain_id") or ""))
        ]

    def list_tools(self) -> list[dict]:
        registry = self.gateway.base_agent.registry
        tools = [to_api_schema(td) for td in registry.list_tools()]
        if not self.is_enterprise() or self.principal.is_owner:
            return tools
        return [
            item for item in tools
            if not self._tool_domain_id(str(item.get("name") or ""))
            or self.can_use_domain(self._tool_domain_id(str(item.get("name") or "")))
        ]

    def list_extensions(self) -> list[dict]:
        result = ExtensionRegistry(self.gateway).list()
        distro_id = self.distro.manifest().id if self.distro is not None else "personal"
        return [{"distro_id": distro_id, **ext} for ext in result]

    def enterprise_directory(self) -> "EnterpriseDirectory":
        self.require_enterprise()
        from hushclaw.enterprise import EnterpriseDirectory
        directory = getattr(self.distro, "directory", None)
        if directory is None:
            raise EnterpriseDistroRequired("enterprise directory unavailable")
        return directory

    def domain_registry(self) -> "DomainRegistry":
        from hushclaw.domains import DomainRegistry
        registry = getattr(self.distro, "domain_registry", None)
        return registry if registry is not None else DomainRegistry()

    def enabled_domains(self) -> list[dict]:
        if not self.is_enterprise():
            return []
        return [
            item for item in self.list_domains()
            if item.get("status", {}).get("enabled")
        ]

    def enterprise_overview(self) -> dict:
        self.require_enterprise()
        directory = self.enterprise_directory()
        domains = self.list_domains()
        audit = self.audit_events(limit=5)
        return {
            "distro": self.distro_manifest(),
            "directory": directory.overview(),
            "domains": {
                "total": len(domains),
                "enabled": len([d for d in domains if d.get("status", {}).get("enabled")]),
                "planned": len([d for d in domains if d.get("manifest", {}).get("status") == "planned"]),
                "business": len(domains),
            },
            "audit": {
                "recent": audit,
            },
            "platform": {
                "foundation": [
                    "Organization Directory",
                    "Position & Reporting Graph",
                    "Identity References",
                    "Runtime Principal",
                    "RBAC PolicyGate",
                    "Audit Events",
                    "Module Catalog",
                    "Shared Memory Scopes",
                ],
                "boundary": "Enterprise foundation manages identity, access, audit, and module lifecycle; domains are business solution packages.",
            },
        }

    def enterprise_settings(self) -> dict:
        self.require_enterprise()
        settings = getattr(self.distro, "settings", None)
        if settings is None:
            settings = {
                "org_name": self.enterprise_directory().snapshot().org.name,
                "default_model_policy": "kernel_default",
                "audit_retention_days": 180,
                "memory_scopes": ["org", "domain", "workspace"],
                "module_install_policy": "owner_only",
            }
            self.distro.settings = settings
        return dict(settings)

    def update_enterprise_settings(self, updates: dict[str, Any]) -> dict:
        self.require_enterprise()
        allowed = {
            "org_name",
            "default_model_policy",
            "audit_retention_days",
            "memory_scopes",
            "module_install_policy",
        }
        current = self.enterprise_settings()
        for key, value in (updates or {}).items():
            if key in allowed:
                current[key] = value
        self.distro.settings = current
        self.record_audit_event("settings.updated", resource={"type": "enterprise_settings", "id": "default"})
        return dict(current)

    def list_org_units(self) -> list[dict]:
        self.require_enterprise()
        return self.enterprise_directory().list_units()

    def list_positions(self) -> list[dict]:
        self.require_enterprise()
        return self.enterprise_directory().list_positions()

    def list_members(self) -> list[dict]:
        self.require_enterprise()
        return self.enterprise_directory().list_members()

    def list_roles(self) -> list[dict]:
        self.require_enterprise()
        return self.enterprise_directory().list_roles()

    def list_role_assignments(self) -> list[dict]:
        self.require_enterprise()
        return self.enterprise_directory().list_role_assignments()

    def list_teams(self) -> list[dict]:
        self.require_enterprise()
        return self.enterprise_directory().list_teams()

    def list_domain_access(self, domain_id: str = "") -> list[dict]:
        self.require_enterprise()
        return self.enterprise_directory().list_domain_access(domain_id)

    def list_enterprise_credentials(self) -> list[dict]:
        self.require_enterprise()
        return self.enterprise_directory().list_credentials()

    def foundation_catalog(self) -> list[dict]:
        self.require_enterprise()
        return [
            {
                "id": "organization_directory",
                "name": "Organization Directory",
                "description": "Org units, positions, members, reporting lines, and teams.",
                "status": "enabled",
                "category": "foundation",
            },
            {
                "id": "identity_access",
                "name": "Identity & Access",
                "description": "Runtime principals, identity references, roles, and assignments.",
                "status": "enabled",
                "category": "foundation",
            },
            {
                "id": "policy_audit",
                "name": "Policy & Audit",
                "description": "RBAC hooks, module governance, and audit event retention.",
                "status": "enabled",
                "category": "foundation",
            },
            {
                "id": "module_catalog",
                "name": "Module Catalog",
                "description": "Installable enterprise business domains such as CRM, HR, and Finance.",
                "status": "enabled",
                "category": "foundation",
            },
        ]

    def upsert_org_unit(self, data: dict[str, Any]) -> dict:
        self.require_enterprise()
        item = self.enterprise_directory().upsert_unit(data)
        self._persist_enterprise_directory()
        self.record_audit_event("directory.unit.upserted", resource={"type": "org_unit", "id": item["id"]})
        return item

    def upsert_position(self, data: dict[str, Any]) -> dict:
        self.require_enterprise()
        item = self.enterprise_directory().upsert_position(data)
        self._persist_enterprise_directory()
        self.record_audit_event("directory.position.upserted", resource={"type": "position", "id": item["id"]})
        return item

    def upsert_member(self, data: dict[str, Any]) -> dict:
        self.require_enterprise()
        item = self.enterprise_directory().upsert_member(data)
        self._persist_enterprise_directory()
        self.record_audit_event("directory.member.upserted", resource={"type": "member", "id": item["id"]})
        return item

    def set_member_password(self, member_id: str, password: str, *, temporary: bool = True) -> dict:
        self.require_enterprise()
        result = self.enterprise_directory().set_member_password(member_id, password, temporary=temporary)
        self._persist_enterprise_directory()
        self.record_audit_event(
            "directory.member.password_set",
            resource={"type": "member", "id": member_id},
            metadata={key: value for key, value in result.items() if key != "error"},
        )
        return result

    def authenticate_enterprise(self, login_id: str, password: str) -> dict:
        self.require_enterprise()
        result = self.enterprise_directory().authenticate(login_id, password)
        self._persist_enterprise_directory()
        if result.get("ok"):
            self.record_audit_event(
                "auth.login",
                resource={"type": "member", "id": result.get("member", {}).get("id", "")},
                metadata={"login_id": str(login_id or "").strip().lower()},
            )
        return result

    def enterprise_session_principal(self, session_id: str, *, workspace_id: str = "") -> RuntimePrincipal | None:
        self.require_enterprise()
        result = self.enterprise_directory().member_for_session(session_id)
        self._persist_enterprise_directory()
        if result is None:
            return None
        member, roles, session = result
        return self.distro.runtime_principal(
            principal_id=member.id,
            workspace_id=workspace_id,
            roles=roles,
            source_channel="webui",
            auth_context={
                "session_id": session.session_id,
                "login_id": member.email or member.identity_ref or member.id,
                "display_name": member.display_name,
            },
        )

    def enterprise_auth_me(self, session_id: str) -> dict:
        self.require_enterprise()
        result = self.enterprise_directory().member_for_session(session_id)
        self._persist_enterprise_directory()
        if result is None:
            return {"ok": False}
        member, roles, session = result
        return {
            "ok": True,
            "member": member.to_dict(),
            "roles": list(roles),
            "session": session.to_dict(),
        }

    def logout_enterprise(self, session_id: str) -> dict:
        self.require_enterprise()
        ok = self.enterprise_directory().logout(session_id)
        self._persist_enterprise_directory()
        return {"ok": ok}

    def deactivate_member(self, member_id: str) -> dict:
        self.require_enterprise()
        ok = self.enterprise_directory().deactivate_member(member_id)
        self._persist_enterprise_directory()
        result = {"ok": ok, "member_id": member_id}
        self.record_audit_event(
            "directory.member.deactivated",
            resource={"type": "member", "id": member_id},
            metadata=result,
        )
        return result

    def upsert_role(self, data: dict[str, Any]) -> dict:
        self.require_enterprise()
        item = self.enterprise_directory().upsert_role(data)
        self._persist_enterprise_directory()
        self.record_audit_event("directory.role.upserted", resource={"type": "role", "id": item["id"]})
        return item

    def assign_role(
        self,
        member_id: str,
        role_id: str,
        *,
        scope: str = "org",
        scope_id: str = "",
    ) -> dict:
        self.require_enterprise()
        item = self.enterprise_directory().assign_role(member_id, role_id, scope=scope, scope_id=scope_id)
        self._persist_enterprise_directory()
        self.record_audit_event(
            "directory.role.assigned",
            resource={"type": "role_assignment", "id": f"{member_id}:{role_id}:{scope}:{scope_id}"},
            metadata=item,
        )
        return item

    def revoke_role(
        self,
        member_id: str,
        role_id: str,
        *,
        scope: str = "org",
        scope_id: str = "",
    ) -> dict:
        self.require_enterprise()
        ok = self.enterprise_directory().revoke_role(member_id, role_id, scope=scope, scope_id=scope_id)
        self._persist_enterprise_directory()
        result = {
            "ok": ok,
            "member_id": member_id,
            "role_id": role_id,
            "scope": scope,
            "scope_id": scope_id or self.enterprise_directory().snapshot().org.id,
        }
        self.record_audit_event(
            "directory.role.revoked",
            resource={"type": "role_assignment", "id": f"{member_id}:{role_id}:{scope}:{scope_id}"},
            metadata=result,
        )
        return result

    def upsert_team(self, data: dict[str, Any]) -> dict:
        self.require_enterprise()
        item = self.enterprise_directory().upsert_team(data)
        self._persist_enterprise_directory()
        self.record_audit_event("directory.team.upserted", resource={"type": "team", "id": item["id"]})
        return item

    def grant_domain_access(
        self,
        domain_id: str,
        subject_type: str,
        subject_id: str,
        *,
        access_level: str = "use",
    ) -> dict:
        self.require_enterprise()
        item = self.enterprise_directory().grant_domain_access(
            domain_id,
            subject_type,
            subject_id,
            access_level=access_level,
        )
        self._persist_enterprise_directory()
        self.record_audit_event(
            "domain.access.granted",
            resource={"type": "domain_access", "id": f"{domain_id}:{subject_type}:{subject_id}"},
            metadata=item,
        )
        return item

    def revoke_domain_access(self, domain_id: str, subject_type: str, subject_id: str) -> dict:
        self.require_enterprise()
        ok = self.enterprise_directory().revoke_domain_access(domain_id, subject_type, subject_id)
        self._persist_enterprise_directory()
        result = {"ok": ok, "domain_id": domain_id, "subject_type": subject_type, "subject_id": subject_id}
        self.record_audit_event(
            "domain.access.revoked",
            resource={"type": "domain_access", "id": f"{domain_id}:{subject_type}:{subject_id}"},
            metadata=result,
        )
        return result

    def _persist_enterprise_directory(self) -> None:
        persist = getattr(self.distro, "persist_directory", None)
        if persist is not None:
            persist()

    def list_domains(self) -> list[dict]:
        if not self.is_enterprise():
            return []
        items = self.domain_registry().list()
        if self.principal.is_owner:
            return items
        return [
            item for item in items
            if item.get("status", {}).get("enabled")
            and self.can_use_domain(str(item.get("manifest", {}).get("id") or ""))
        ]

    def accessible_domains(self) -> list[dict]:
        self.require_enterprise()
        return self.list_domains()

    def can_use_domain(self, domain_id: str) -> bool:
        if not self.is_enterprise() or not domain_id:
            return False
        if self.principal.is_owner:
            return True
        return self.enterprise_directory().can_use_domain(
            self.principal.principal_id,
            domain_id,
            self.principal.roles,
        )

    def can_admin_domain(self, domain_id: str) -> bool:
        if not self.is_enterprise() or not domain_id:
            return False
        if self.principal.is_owner:
            return True
        return self.enterprise_directory().can_admin_domain(
            self.principal.principal_id,
            domain_id,
            self.principal.roles,
        )

    def _tool_domain_id(self, tool_name: str) -> str:
        domain_id = tool_name.split(".", 1)[0] if "." in tool_name else ""
        return domain_id if domain_id and self.domain_registry().get(domain_id) else ""

    def domain_manifest(self, domain_id: str) -> dict:
        self.require_enterprise()
        return self.domain_registry().manifest(domain_id)

    def domain_status(self, domain_id: str) -> dict:
        self.require_enterprise()
        return self.domain_registry().status(domain_id)

    def domain_dependency_status(self, domain_id: str) -> dict:
        self.require_enterprise()
        return self.domain_registry().dependency_status(domain_id)

    def install_domain(self, domain_id: str, *, scope: str = "org") -> dict:
        self.require_enterprise()
        result = self.domain_registry().install(domain_id, scope=scope)
        self.record_audit_event(
            "module.installed",
            resource={"type": "domain", "id": domain_id, "scope": scope},
            metadata={"ok": result.get("ok", False), "message": result.get("message", "")},
        )
        return result

    def enable_domain(self, domain_id: str, *, scope: str = "org") -> dict:
        self.require_enterprise()
        result = self.domain_registry().enable(domain_id, scope=scope)
        sync_tools = getattr(self.distro, "sync_domain_tools", None)
        if sync_tools is not None:
            sync_tools(domain_id)
        sync = getattr(self.distro, "sync_domain_agents", None)
        if sync is not None:
            sync(domain_id)
        self.record_audit_event(
            "module.enabled",
            resource={"type": "domain", "id": domain_id, "scope": scope},
            metadata={"ok": result.get("ok", False), "message": result.get("message", "")},
        )
        return result

    def disable_domain(self, domain_id: str, *, scope: str = "org") -> dict:
        self.require_enterprise()
        result = self.domain_registry().disable(domain_id, scope=scope)
        sync = getattr(self.distro, "sync_domain_agents", None)
        if sync is not None:
            sync(domain_id)
        self.record_audit_event(
            "module.disabled",
            resource={"type": "domain", "id": domain_id, "scope": scope},
            metadata={"ok": result.get("ok", False), "message": result.get("message", "")},
        )
        return result

    def domain_config(self, domain_id: str) -> dict:
        self.require_enterprise()
        return self.domain_registry().config(domain_id)

    def update_domain_config(self, domain_id: str, config: dict[str, Any]) -> dict:
        self.require_enterprise()
        result = self.domain_registry().update_config(domain_id, config)
        self.record_audit_event(
            "module.config.updated",
            resource={"type": "domain", "id": domain_id},
            metadata={"ok": result.get("ok", False)},
        )
        return result

    def domain_records(self, domain_id: str, dataset: str, *, limit: int = 50) -> list[dict]:
        self.require_enterprise()
        domain = self.domain_registry().get(domain_id)
        if domain is None:
            return []
        return domain.list_records(dataset, limit=limit)

    def domain_create_record(self, domain_id: str, dataset: str, data: dict[str, Any]) -> dict:
        self.require_enterprise()
        domain = self.domain_registry().get(domain_id)
        if domain is None:
            return {"ok": False, "domain_id": domain_id, "dataset": dataset, "message": f"Unknown domain: {domain_id}"}
        result = domain.create_record(dataset, data, actor_id=self.principal.principal_id)
        self.record_audit_event(
            "domain.record.mutated",
            resource={"type": "domain_dataset", "id": f"{domain_id}:{dataset}"},
            metadata={"ok": result.get("ok", False), "domain_id": domain_id, "dataset": dataset},
        )
        return result

    def domain_events(
        self,
        domain_id: str,
        *,
        entity_type: str = "",
        entity_id: str = "",
        limit: int = 50,
    ) -> list[dict]:
        self.require_enterprise()
        domain = self.domain_registry().get(domain_id)
        if domain is None:
            return []
        return domain.list_events(entity_type=entity_type, entity_id=entity_id, limit=limit)

    def domain_work_items(
        self,
        domain_id: str,
        *,
        state_type: str = "",
        status: str = "",
        limit: int = 20,
    ) -> list[dict]:
        self.require_enterprise()
        domain = self.domain_registry().get(domain_id)
        if domain is None:
            return []
        return domain.list_work_items(state_type=state_type, status=status, limit=limit)

    def domain_execute_action(self, domain_id: str, action: str, payload: dict[str, Any]) -> dict:
        self.require_enterprise()
        domain = self.domain_registry().get(domain_id)
        if domain is None:
            return {"ok": False, "domain_id": domain_id, "action": action, "message": f"Unknown domain: {domain_id}"}
        result = domain.execute_action(action, payload, actor_id=self.principal.principal_id)
        self.record_audit_event(
            "domain.action.executed",
            resource={"type": "domain_action", "id": f"{domain_id}:{action}"},
            metadata={"ok": result.get("ok", False), "domain_id": domain_id, "action": action},
        )
        return result

    def crm_records(self, entity_type: str, *, limit: int = 50) -> list[dict]:
        return self.domain_records("crm", entity_type, limit=limit)

    def crm_create_record(self, entity_type: str, data: dict[str, Any]) -> dict:
        result = self.domain_create_record("crm", entity_type, data)
        return {"entity_type": entity_type, **result}

    def crm_update_outbound_draft_status(self, draft_id: str, status: str) -> dict:
        result = self.domain_execute_action(
            "crm",
            "outbound_draft.set_status",
            {"draft_id": draft_id, "status": status},
        )
        return {"draft_id": draft_id, **result}

    def crm_events(self, *, entity_type: str = "", entity_id: str = "", limit: int = 50) -> list[dict]:
        return self.domain_events("crm", entity_type=entity_type, entity_id=entity_id, limit=limit)

    def crm_next_actions(self, *, limit: int = 20) -> list[dict]:
        return self.domain_work_items("crm", state_type="next_action", status="suggested", limit=limit)

    def crm_update_next_action_status(self, state_id: str, status: str) -> dict:
        result = self.domain_execute_action(
            "crm",
            "next_action.set_status",
            {"state_id": state_id, "status": status},
        )
        return {"state_id": state_id, **result}

    def memory_port(self) -> SQLiteMemoryPort:
        return SQLiteMemoryPort(self.gateway.memory)

    def search_memory(self, query: str, *, scopes: list[str] | None = None, limit: int = 5) -> list[dict]:
        return self.memory_port().search(query, scopes=scopes, principal=self.principal, limit=limit)

    def remember(
        self,
        content: str,
        *,
        scope: str = "global",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self.memory_port().remember(content, scope=scope, principal=self.principal, metadata=metadata)

    def audit_events(self, *, session_id: str = "", limit: int = 200) -> list[dict]:
        mem = self.gateway.memory
        if session_id:
            events = mem.session_log.session_events(session_id, limit=limit)
            return [e for e in events if str(e.get("type", "")).startswith("audit:")]
        event_log = getattr(mem, "session_log", None)
        if event_log is None:
            return []
        if hasattr(event_log, "type_prefix_events"):
            return event_log.type_prefix_events("audit:", limit=limit)
        events = event_log.events_in_window(limit=max(1, int(limit) * 4)) if hasattr(event_log, "events_in_window") else []
        audit = [e for e in events if str(e.get("type", "")).startswith("audit:")]
        return sorted(audit, key=lambda item: int(item.get("ts") or 0), reverse=True)[: int(limit)]

    def build_audit_event(self, event_type: str, **kwargs: Any) -> AuditEvent:
        return AuditEvent(event_type=event_type, principal=self.principal, **kwargs)

    def record_audit_event(self, event_type: str, **kwargs: Any) -> str:
        event = self.build_audit_event(event_type, **kwargs)
        return append_audit_event(self.gateway.memory, event)

    # Session APIs

    def list_sessions(
        self,
        *,
        limit: int,
        offset: int = 0,
        include_scheduled: bool = True,
        max_idle_days: int = 0,
        workspace: str | None = None,
    ) -> tuple[list[dict], bool]:
        fetch_limit = max(1, int(limit)) + 1
        items = self.gateway.memory.list_sessions(
            limit=fetch_limit,
            include_scheduled=include_scheduled,
            max_idle_days=max(0, int(max_idle_days)),
            workspace=workspace,
            offset=max(0, int(offset)),
        )
        has_more = len(items) > int(limit)
        return (items[: int(limit)] if has_more else items), has_more

    def session_history(self, session_id: str) -> dict:
        mem = self.gateway.memory
        return {
            "turns": mem.load_session_history(session_id),
            "summary": mem.load_session_summary(session_id) if session_id else None,
            "lineage": mem.get_session_lineage(session_id) if session_id else [],
        }

    def search_sessions(
        self,
        *,
        query: str,
        limit: int = 20,
        include_scheduled: bool = True,
        workspace: str | None = None,
    ) -> list[dict]:
        return self.gateway.memory.search_sessions(
            query=query,
            limit=max(1, int(limit)),
            include_scheduled=include_scheduled,
            workspace=workspace,
        )

    def session_lineage(self, session_id: str) -> list[dict]:
        return self.gateway.memory.get_session_lineage(session_id) if session_id else []

    def delete_session(self, session_id: str) -> bool:
        return self.gateway.memory.delete_session(session_id) if session_id else False

    def set_message_state(self, message_id: str, *, session_id: str, action: str) -> dict:
        kwargs: dict[str, Any]
        if action == "hide":
            kwargs = {"hidden": True}
        elif action == "exclude":
            kwargs = {"excluded": True}
        elif action == "delete":
            kwargs = {"hidden": True, "excluded": True, "purged": True}
        else:
            return {"ok": False, "error": "Unknown message state action", "message_id": message_id}
        ok = self.gateway.memory.set_message_state(message_id, session_id=session_id, **kwargs) if message_id else False
        derived_deleted = {}
        if ok and action == "delete":
            derived_deleted = self.gateway.memory.delete_message_derived_data(message_id)
        if ok:
            self.gateway.clear_all_cached_loops()
        return {
            "ok": ok,
            "message_id": self.gateway.memory.canonical_message_id(message_id) if message_id else "",
            "session_id": session_id,
            "action": action,
            "derived_deleted": derived_deleted,
        }

    # Memory APIs

    @staticmethod
    def normalize_note_payload(item: dict) -> dict:
        out = dict(item or {})
        created = out.get("created")
        modified = out.get("modified")
        out["created_at"] = int(created or modified or 0) if (created or modified) else 0
        if modified is not None:
            out["updated_at"] = int(modified)
        return out

    @staticmethod
    def _is_auto_extract_note(item: dict) -> bool:
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return "_auto_extract" in tags

    @staticmethod
    def _is_system_note(item: dict) -> bool:
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if bool(SYSTEM_MEMORY_TAGS & set(tags)):
            return True
        return item.get("memory_kind") in {"telemetry", "session_memory"}

    def list_memories(
        self,
        *,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
        include_auto: bool = False,
        memory_kinds: set[str] | None = None,
        workspace: str = "",
    ) -> tuple[list[dict], bool]:
        agent = self.gateway.base_agent
        exclude_tags = sorted(SYSTEM_MEMORY_TAGS)
        if not include_auto:
            exclude_tags.append("_auto_extract")
        fetch_limit = max(1, int(limit)) + 1
        if query:
            items = agent.search(query, limit=fetch_limit, include_kinds=memory_kinds)
            items = [m for m in items if not self._is_system_note(m)]
            if not include_auto:
                items = [m for m in items if not self._is_auto_extract_note(m)]
        elif workspace:
            items = agent.memory.list_recent_notes_by_scopes(
                scopes=["global", f"workspace:{workspace}"],
                limit=fetch_limit,
                offset=max(0, int(offset)),
                exclude_tags=exclude_tags,
                include_kinds=memory_kinds,
            )
        else:
            items = agent.list_memories(
                limit=fetch_limit,
                offset=max(0, int(offset)),
                exclude_tags=exclude_tags,
                include_kinds=memory_kinds,
            )
        has_more = len(items) > int(limit)
        if has_more:
            items = items[: int(limit)]
        return [self.normalize_note_payload(m) for m in items], has_more

    def delete_memory(self, note_id: str) -> bool:
        return self.gateway.base_agent.forget(note_id) if note_id else False

    def learning_state(self, *, reflection_limit: int = 8, skill_outcome_limit: int = 10) -> dict:
        mem = self.gateway.memory
        return {
            "profile_snapshot": mem.user_profile.get_profile_snapshot(),
            "profile_text": mem.user_profile.render_profile_context(max_chars=1400),
            "reflections": mem.list_reflections(limit=reflection_limit),
            "skill_outcomes": mem.list_recent_skill_outcomes(limit=skill_outcome_limit),
        }

    def memory_overview(self, *, session_id: str = "", reflection_limit: int = 30) -> dict:
        mem = self.gateway.memory
        visible_kinds = USER_VISIBLE_MEMORY_KINDS
        exclude_tags = sorted(SYSTEM_MEMORY_TAGS | {"_auto_extract"})
        return {
            "profile_facts": mem.user_profile.list_facts(limit=200),
            "beliefs": mem.list_belief_models(),
            "reflections": mem.list_reflections(limit=reflection_limit),
            "recent_notes": [
                self.normalize_note_payload(n)
                for n in mem.list_recent_notes(limit=6, exclude_tags=exclude_tags, include_kinds=visible_kinds)
            ],
            "working_state": mem.load_session_working_state(session_id) if session_id else "",
            "global_working_state": mem.load_global_working_state() or "",
        }

    def workspace_briefing_inputs(self, *, workspace: str) -> dict:
        mem = self.gateway.memory
        return {
            "sessions": mem.list_sessions(limit=6, include_scheduled=False, workspace=workspace, offset=0),
            "todos": mem.list_todos(status="pending"),
            "scheduled": mem.list_scheduled_tasks(),
            "reflections": mem.list_reflections(limit=5),
        }

    def accept_briefing_create_todo(self, todo: dict, *, fallback_title: str = "Briefing follow-up") -> dict:
        return self.gateway.memory.add_todo(
            title=str(todo.get("title") or fallback_title),
            notes=str(todo.get("notes") or "Created from proactive workspace briefing."),
            priority=int(todo.get("priority") or 0),
            tags=todo.get("tags") or ["briefing"],
        )

    def get_session_brief(self, session_id: str) -> dict | None:
        return self.gateway.memory.get_session_brief(session_id)

    def get_note(self, note_id: str) -> dict | None:
        return self.gateway.memory.get_note(note_id)

    def list_belief_models(self, scopes: list[str] | None = None) -> list[dict]:
        return self.gateway.memory.list_belief_models(scopes=scopes)

    def rebuild_belief_models(self, *, dry_run: bool = False, scopes: list[str] | None = None) -> dict:
        return self.gateway.memory.rebuild_belief_models(dry_run=dry_run, scopes=scopes)

    def list_profile_facts(self, *, limit: int = 200) -> list[dict]:
        return self.gateway.memory.user_profile.list_facts(limit=limit)

    def delete_profile_fact(self, fact_id: str) -> bool:
        return self.gateway.memory.user_profile.delete_fact(fact_id) if fact_id else False

    # Scheduled task and todo APIs

    def list_scheduled_tasks(self) -> list[dict]:
        return self.gateway.memory.list_scheduled_tasks()

    def create_scheduled_task(self, data: dict) -> dict | None:
        mem = self.gateway.memory
        task_id = mem.add_scheduled_task(
            cron=data.get("cron", ""),
            prompt=data.get("prompt", ""),
            agent=data.get("agent", ""),
            run_once=bool(data.get("run_once", False)),
            title=data.get("title", ""),
        )
        return next((t for t in mem.list_scheduled_tasks() if t["id"] == task_id), None)

    def toggle_scheduled_task(self, task_id: str, enabled: bool) -> bool:
        return self.gateway.memory.toggle_scheduled_task(task_id, enabled)

    def delete_scheduled_task(self, task_id: str) -> bool:
        return self.gateway.memory.delete_scheduled_task(task_id)

    def list_todos(self, status: str | None = None) -> list[dict]:
        return self.gateway.memory.list_todos(status=status)

    def create_todo(self, data: dict) -> dict:
        due_at = data.get("due_at")
        return self.gateway.memory.add_todo(
            title=data.get("title", ""),
            notes=data.get("notes", ""),
            priority=int(data.get("priority", 0)),
            due_at=int(due_at) if due_at else None,
            tags=data.get("tags") or [],
        )

    def update_todo(self, todo_id: str, fields: dict) -> dict | None:
        return self.gateway.memory.update_todo(todo_id, **fields)

    def delete_todo(self, todo_id: str) -> bool:
        return self.gateway.memory.delete_todo(todo_id)

    # ── Briefing + Memory-overview payload builders ────────────────────────

    @staticmethod
    def _briefing_session_source(item: dict) -> dict:
        return {
            "type": "session",
            "id": str(item.get("session_id") or ""),
            "title": str(item.get("title") or item.get("last_preview") or "Untitled session"),
            "updated": int(item.get("last_turn") or item.get("updated") or 0),
        }

    @staticmethod
    def _briefing_todo_source(item: dict) -> dict:
        return {
            "type": "todo",
            "id": str(item.get("todo_id") or ""),
            "title": str(item.get("title") or "Todo"),
            "updated": int(item.get("updated") or item.get("created") or 0),
        }

    @staticmethod
    def _workspace_label(workspace: str) -> str:
        return workspace or "Default"

    @staticmethod
    def _profile_fact_value(item: dict) -> str:
        import json as _json
        raw = item.get("value_json") or {}
        if isinstance(raw, dict):
            return str(raw.get("summary") or raw.get("value") or _json.dumps(raw, ensure_ascii=False))
        return str(raw or "")

    @staticmethod
    def _format_task_fingerprint(value: str) -> str:
        raw = str(value or "general_assistance").strip() or "general_assistance"
        return " ".join(part.capitalize() for part in raw.split("_") if part)

    @staticmethod
    def _session_source_payload(os_svc: "AgentOSService", session_id: str) -> dict | None:
        sid = str(session_id or "").strip()
        if not sid:
            return None
        try:
            brief = os_svc.get_session_brief(sid)
        except Exception:
            brief = None
        brief = brief or {"session_id": sid}
        return {
            "type": "session",
            "session_id": sid,
            "title": brief.get("title") or f"Session {sid[-8:]}",
            "kind": brief.get("kind", ""),
            "workspace": brief.get("workspace", ""),
            "last_turn": int(brief.get("last_turn") or 0),
            "turn_count": int(brief.get("turn_count") or 0),
        }

    @staticmethod
    def _note_source_payload(os_svc: "AgentOSService", note_id: str) -> dict | None:
        nid = str(note_id or "").strip()
        if not nid:
            return None
        try:
            note = os_svc.get_note(nid)
        except Exception:
            note = None
        if not note:
            return {"type": "note", "note_id": nid, "title": f"Memory {nid[-8:]}"}
        return {
            "type": "note",
            "note_id": nid,
            "title": note.get("title") or f"Memory {nid[-8:]}",
            "note_type": note.get("note_type", ""),
            "memory_kind": note.get("memory_kind", ""),
            "created": int(note.get("created") or 0),
            "updated": int(note.get("modified") or 0),
        }

    @classmethod
    def _profile_fact_payload(cls, os_svc: "AgentOSService", fact: dict) -> dict:
        return {
            "fact_id": fact.get("fact_id", ""),
            "category": fact.get("category", ""),
            "key": fact.get("key", ""),
            "value": cls._profile_fact_value(fact),
            "confidence": float(fact.get("confidence") or 0.0),
            "updated": int(fact.get("updated") or 0),
            "source": cls._session_source_payload(os_svc, fact.get("source_session_id", "")),
            **classify_profile_fact(fact),
        }

    @classmethod
    def _belief_payload(cls, os_svc: "AgentOSService", belief: dict) -> dict:
        out = dict(belief)
        entries = []
        for entry in (belief.get("entries") or [])[:10]:
            item = dict(entry)
            item["source"] = cls._note_source_payload(os_svc, item.get("note_id", ""))
            entries.append(item)
        out["entries"] = entries
        out["entry_count"] = len(belief.get("entries") or [])
        out["display_domain"] = (
            "Unclassified Signals" if str(out.get("domain") or "") == "general" else out.get("domain", "")
        )
        out.update(classify_belief_model(out))
        return out

    @classmethod
    def _reflection_payload(cls, os_svc: "AgentOSService", reflection: dict) -> dict:
        out = dict(reflection)
        out["source"] = cls._session_source_payload(os_svc, reflection.get("session_id", ""))
        out.update(classify_reflection(reflection))
        return out

    def build_workspace_briefing_payload(self, *, workspace: str, now: int) -> dict:
        briefing_inputs = self.workspace_briefing_inputs(workspace=workspace)
        sessions = briefing_inputs["sessions"]
        todos = briefing_inputs["todos"]
        scheduled = briefing_inputs["scheduled"]
        reflections = briefing_inputs["reflections"]

        priority_todos = [t for t in todos if int(t.get("priority") or 0)]
        due_todos = [t for t in todos if t.get("due_at") and int(t.get("due_at") or 0) <= now + 86400]
        enabled_scheduled = [t for t in scheduled if int(t.get("enabled", 1))]
        failures = [r for r in reflections if not bool(r.get("success"))]

        focus_items: list[dict] = []
        for item in sessions[:3]:
            focus_items.append({
                "title": item.get("title") or item.get("last_preview") or "Recent session",
                "detail": item.get("last_preview") or item.get("title") or "",
                "source": self._briefing_session_source(item),
            })
        for item in priority_todos[:2]:
            focus_items.append({
                "title": item.get("title") or "High priority todo",
                "detail": "High priority todo",
                "source": self._briefing_todo_source(item),
            })

        risks: list[dict] = []
        if due_todos:
            risks.append({
                "title": f"{len(due_todos)} todo(s) due soon",
                "detail": "Review pending commitments before starting new work.",
                "severity": "medium",
            })
        if failures:
            risks.append({
                "title": "Recent failed reflection detected",
                "detail": failures[0].get("lesson") or failures[0].get("outcome") or "Review the latest failed task before repeating the workflow.",
                "severity": "medium",
            })
        if not sessions and not todos:
            risks.append({
                "title": "No active workspace signal yet",
                "detail": "Start a conversation or add todos so HushClaw can build a sharper briefing.",
                "severity": "low",
            })

        suggestions: list[dict] = []
        if sessions:
            latest = sessions[0]
            title = latest.get("title") or latest.get("last_preview") or "latest work"
            suggestions.append({
                "id": "continue-" + str(latest.get("session_id") or make_id())[:12],
                "type": "continue_work",
                "title": "Continue recent work",
                "body": f"Pick up from: {title}",
                "action": "chat_prompt",
                "prompt": f"Continue the recent workspace thread: {title}. Summarize the current state, identify the next concrete step, then proceed.",
                "sources": [self._briefing_session_source(latest)],
            })
        if priority_todos:
            todo = priority_todos[0]
            suggestions.append({
                "id": "todo-focus-" + str(todo.get("todo_id") or make_id())[:12],
                "type": "review_risk",
                "title": "Focus high-priority todo",
                "body": todo.get("title") or "A high-priority todo is still open.",
                "action": "chat_prompt",
                "prompt": f"Help me make progress on this high-priority todo: {todo.get('title')}. Start by proposing a short execution plan.",
                "sources": [self._briefing_todo_source(todo)],
            })
        if not todos and sessions:
            latest = sessions[0]
            suggestions.append({
                "id": "create-followup-" + str(latest.get("session_id") or make_id())[:12],
                "type": "create_todo",
                "title": "Capture a follow-up todo",
                "body": "Turn the latest thread into one concrete follow-up item.",
                "action": "create_todo",
                "todo": {
                    "title": "Follow up: " + (latest.get("title") or latest.get("last_preview") or "recent HushClaw thread")[:80],
                    "notes": "Created from proactive workspace briefing.",
                    "priority": 0,
                    "tags": ["briefing"],
                },
                "sources": [self._briefing_session_source(latest)],
            })
        if not enabled_scheduled:
            suggestions.append({
                "id": f"schedule-briefing-{workspace or 'default'}",
                "type": "schedule_followup",
                "title": "Create a daily workspace briefing",
                "body": "Schedule a morning review so this workspace starts with context.",
                "action": "chat_prompt",
                "prompt": "Help me create a daily scheduled task that generates a concise workspace briefing every morning.",
                "sources": [],
            })

        return {
            "workspace": workspace,
            "created_at": now,
            "summary": (
                f"{self._workspace_label(workspace)} has {len(sessions)} recent session(s), "
                f"{len(todos)} pending todo(s), and {len(enabled_scheduled)} active scheduled task(s)."
            ),
            "focus_items": focus_items[:5],
            "risks": risks[:4],
            "suggestions": suggestions[:5],
            "sources": {
                "sessions": [self._briefing_session_source(s) for s in sessions[:5]],
                "todos": [self._briefing_todo_source(t) for t in todos[:5]],
            },
            "scheduled": [
                {
                    "cron": t.get("cron", ""),
                    "prompt_preview": (t.get("prompt") or "")[:80],
                    "enabled": bool(int(t.get("enabled", 1))),
                    "task_id": t.get("task_id") or t.get("id") or "",
                }
                for t in enabled_scheduled[:5]
            ],
        }

    def build_memory_overview_payload(self, *, session_id: str, reflection_limit: int) -> dict:
        overview = self.memory_overview(session_id=session_id, reflection_limit=reflection_limit)
        profile_facts = overview["profile_facts"]
        profile_by_category: dict[str, int] = {}
        for fact in profile_facts:
            category = str(fact.get("category") or "misc")
            profile_by_category[category] = profile_by_category.get(category, 0) + 1
        high_confidence = sorted(
            profile_facts,
            key=lambda f: (float(f.get("confidence") or 0.0), int(f.get("updated") or 0)),
            reverse=True,
        )[:18]

        beliefs = overview["beliefs"]
        reflections = overview["reflections"]
        recent_notes = []
        for n in overview["recent_notes"]:
            normalized = self.normalize_note_payload(n)
            recent_notes.append({**normalized, **classify_note(normalized)})
        working_state = overview["working_state"]

        task_counts: dict[str, int] = {}
        for r in reflections:
            label = self._format_task_fingerprint(r.get("task_fingerprint", ""))
            task_counts[label] = task_counts.get(label, 0) + 1

        return {
            "taxonomy": {
                "context": context_taxonomy(has_working_state=bool(working_state)),
                "conceptual_priority": ["now", "long_term", "mid_term", "recent", "learning"],
                "injection_order": ["date", "user_notes", "profile", "belief_models", "working_state", "references", "recalled_memories"],
            },
            "profile": {
                "total": len(profile_facts),
                "top_categories": [
                    {"category": k, "count": v}
                    for k, v in sorted(profile_by_category.items(), key=lambda kv: kv[1], reverse=True)[:5]
                ],
                "high_confidence_facts": [
                    self._profile_fact_payload(self, f)
                    for f in high_confidence
                ],
            },
            "beliefs": {
                "total": len(beliefs),
                "dirty_count": sum(1 for b in beliefs if int(b.get("dirty") or 0)),
                "top_domains": [
                    self._belief_payload(self, {
                        "domain": b.get("domain", ""),
                        "summary": b.get("summary") or b.get("latest") or "",
                        "trajectory": b.get("trajectory") or "",
                        "signals": b.get("signals") or [],
                        "entries": b.get("entries") or [],
                        "dirty": int(b.get("dirty") or 0),
                        "updated": int(b.get("updated") or 0),
                    })
                    for b in beliefs[:5]
                ],
            },
            "reflections": {
                "total_recent": len(reflections),
                "success_count": sum(1 for r in reflections if bool(r.get("success"))),
                "failure_count": sum(1 for r in reflections if not bool(r.get("success"))),
                "top_task_types": [
                    {"task_type": k, "count": v}
                    for k, v in sorted(task_counts.items(), key=lambda kv: kv[1], reverse=True)[:4]
                ],
                "latest_lessons": [
                    self._reflection_payload(self, {
                        "lesson": r.get("lesson") or r.get("outcome") or "",
                        "strategy_hint": r.get("strategy_hint") or "",
                        "success": bool(r.get("success")),
                        "task_type": self._format_task_fingerprint(r.get("task_fingerprint", "")),
                        "session_id": r.get("session_id") or "",
                        "created": int(r.get("created") or 0),
                    })
                    for r in reflections[:5]
                ],
            },
            "memories": {
                "recent_items": recent_notes,
            },
        }
