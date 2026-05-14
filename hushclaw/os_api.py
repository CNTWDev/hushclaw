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
from hushclaw.runtime.audit import AuditEvent, append_audit_event
from hushclaw.runtime.principal import RuntimePrincipal, current_principal
from hushclaw.tools.base import to_api_schema


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
        return self.gateway.list_agents()

    def list_tools(self) -> list[dict]:
        registry = self.gateway.base_agent.registry
        return [to_api_schema(td) for td in registry.list_tools()]

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
                "foundation": len([d for d in domains if d.get("manifest", {}).get("module_type") == "foundation"]),
                "business": len([d for d in domains if d.get("manifest", {}).get("module_type") == "business_domain"]),
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
                "boundary": "Kernel knows domain contracts, not business semantics.",
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

    def _persist_enterprise_directory(self) -> None:
        persist = getattr(self.distro, "persist_directory", None)
        if persist is not None:
            persist()

    def list_domains(self) -> list[dict]:
        if not self.is_enterprise():
            return []
        return self.domain_registry().list()

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
        self.record_audit_event(
            "module.enabled",
            resource={"type": "domain", "id": domain_id, "scope": scope},
            metadata={"ok": result.get("ok", False), "message": result.get("message", "")},
        )
        return result

    def disable_domain(self, domain_id: str, *, scope: str = "org") -> dict:
        self.require_enterprise()
        result = self.domain_registry().disable(domain_id, scope=scope)
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

    def crm_records(self, entity_type: str, *, limit: int = 50) -> list[dict]:
        self.require_enterprise()
        domain = self.domain_registry().get("crm")
        store = getattr(domain, "store", None)
        if store is None:
            return []
        return store.list(entity_type, limit=limit)

    def crm_events(self, *, entity_type: str = "", entity_id: str = "", limit: int = 50) -> list[dict]:
        self.require_enterprise()
        domain = self.domain_registry().get("crm")
        store = getattr(domain, "store", None)
        if store is None:
            return []
        return store.events(entity_type=entity_type, entity_id=entity_id, limit=limit)

    def crm_next_actions(self, *, limit: int = 20) -> list[dict]:
        self.require_enterprise()
        domain = self.domain_registry().get("crm")
        store = getattr(domain, "store", None)
        if store is None:
            return []
        return store.next_actions(limit=limit)

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
            events = mem.events.session_events(session_id, limit=limit)
            return [e for e in events if str(e.get("type", "")).startswith("audit:")]
        event_log = getattr(mem, "events", None)
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
        else:
            return {"ok": False, "error": "Unknown message state action", "message_id": message_id}
        ok = self.gateway.memory.set_message_state(message_id, session_id=session_id, **kwargs) if message_id else False
        if ok:
            self.gateway.clear_all_cached_loops()
        return {
            "ok": ok,
            "message_id": self.gateway.memory.canonical_message_id(message_id) if message_id else "",
            "session_id": session_id,
            "action": action,
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
