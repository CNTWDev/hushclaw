"""Domain runtime contract.

AgentOS knows this contract only. Concrete business semantics such as CRM leads
or HR candidates must live in domain packages, never in the kernel.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class DomainManifest:
    id: str
    name: str
    description: str = ""
    module_type: str = "business_domain"  # foundation | business_domain | integration
    dependencies: tuple[str, ...] = ()
    platform_requirements: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    datasets: tuple[dict[str, Any], ...] = ()
    event_types: tuple[str, ...] = ()
    workflows: tuple[dict[str, Any], ...] = ()
    policies: tuple[dict[str, Any], ...] = ()
    entity_types: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()
    admin_routes: tuple[str, ...] = ()
    workspace_routes: tuple[str, ...] = ()
    ui_entries: tuple[str, ...] = ()
    ui_facets: tuple[dict[str, Any], ...] = ()
    required_permissions: tuple[str, ...] = ()
    status: str = "available"  # available | planned | disabled
    category: str = "business"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "module_type": self.module_type,
            "dependencies": list(self.dependencies),
            "platform_requirements": list(self.platform_requirements),
            "capabilities": list(self.capabilities),
            "datasets": [dict(item) for item in self.datasets],
            "event_types": list(self.event_types),
            "workflows": [dict(item) for item in self.workflows],
            "policies": [dict(item) for item in self.policies],
            "entity_types": list(self.entity_types),
            "tools": list(self.tools),
            "agents": list(self.agents),
            "admin_routes": list(self.admin_routes),
            "workspace_routes": list(self.workspace_routes),
            "ui_entries": list(self.ui_entries),
            "ui_facets": [dict(item) for item in self.ui_facets],
            "required_permissions": list(self.required_permissions),
            "status": self.status,
            "category": self.category,
        }


class DomainRuntime(Protocol):
    def manifest(self) -> DomainManifest: ...

    def install(self, scope: str = "org") -> dict[str, Any]:
        """Install the domain module into a scope."""
        ...

    def enable(self, scope: str = "org") -> dict[str, Any]:
        """Enable the domain module for future sessions."""
        ...

    def disable(self, scope: str = "org") -> dict[str, Any]:
        """Disable the domain module for future sessions."""
        ...

    def tools(self) -> list[Any]:
        """Return domain tool definitions or callables for registration."""
        ...

    def agents(self) -> list[dict[str, Any]]:
        """Return domain-provided agent definitions."""
        ...

    def context_providers(self) -> list[Any]:
        """Return context providers keyed by domain entity refs."""
        ...

    def policy_rules(self) -> list[Any]:
        """Return domain policy rules to be installed by the distro."""
        ...

    def status(self) -> dict[str, Any]:
        """Return install/config/runtime status for this domain."""
        ...

    def config(self) -> dict[str, Any]:
        """Return admin-editable domain configuration."""
        ...

    def update_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Merge admin configuration for this domain."""
        ...

    def list_records(self, dataset: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return records from a domain-owned dataset."""
        ...

    def create_record(self, dataset: str, data: dict[str, Any], *, actor_id: str = "") -> dict[str, Any]:
        """Create or update a domain-owned dataset record."""
        ...

    def list_events(
        self,
        *,
        entity_type: str = "",
        entity_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return domain-owned business events."""
        ...

    def list_work_items(
        self,
        *,
        state_type: str = "",
        status: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return domain-owned work items such as next actions or approvals."""
        ...

    def execute_action(self, action: str, payload: dict[str, Any], *, actor_id: str = "") -> dict[str, Any]:
        """Execute a domain-defined action after AgentOS policy checks."""
        ...


@dataclass(slots=True)
class StaticDomainRuntime:
    """Read-only placeholder domain used to establish the enterprise substrate."""

    _manifest: DomainManifest
    installed: bool = False
    configured: bool = False
    enabled: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def manifest(self) -> DomainManifest:
        return self._manifest

    def install(self, scope: str = "org") -> dict[str, Any]:
        if self._manifest.status == "planned":
            return {
                "ok": False,
                "domain_id": self._manifest.id,
                "message": "Domain module is planned and cannot be installed yet.",
                "scope": scope,
            }
        self.installed = True
        self.metadata["scope"] = scope
        return {"ok": True, "domain_id": self._manifest.id, "message": "Domain module installed.", "scope": scope}

    def enable(self, scope: str = "org") -> dict[str, Any]:
        if self._manifest.status == "planned":
            return {
                "ok": False,
                "domain_id": self._manifest.id,
                "message": "Domain module is planned and cannot be enabled yet.",
                "scope": scope,
            }
        self.installed = True
        self.enabled = True
        self.configured = True
        self.metadata["scope"] = scope
        return {"ok": True, "domain_id": self._manifest.id, "message": "Domain module enabled.", "scope": scope}

    def disable(self, scope: str = "org") -> dict[str, Any]:
        self.enabled = False
        self.metadata["scope"] = scope
        return {"ok": True, "domain_id": self._manifest.id, "message": "Domain module disabled.", "scope": scope}

    def tools(self) -> list[Any]:
        return []

    def agents(self) -> list[dict[str, Any]]:
        return []

    def context_providers(self) -> list[Any]:
        return []

    def policy_rules(self) -> list[Any]:
        return []

    def status(self) -> dict[str, Any]:
        return {
            "domain_id": self._manifest.id,
            "installed": self.installed,
            "enabled": self.enabled,
            "configured": self.configured,
            "status": self._manifest.status,
            "module_type": self._manifest.module_type,
            "dependencies": list(self._manifest.dependencies),
            "admin_routes": list(self._manifest.admin_routes),
            "workspace_routes": list(self._manifest.workspace_routes),
            "metadata": dict(self.metadata),
        }

    def config(self) -> dict[str, Any]:
        return dict(self.metadata.get("config") or {})

    def update_config(self, config: dict[str, Any]) -> dict[str, Any]:
        current = self.config()
        current.update({str(k): v for k, v in (config or {}).items()})
        self.metadata["config"] = current
        self.configured = True
        return {"ok": True, "domain_id": self._manifest.id, "config": dict(current)}

    def list_records(self, dataset: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return []

    def create_record(self, dataset: str, data: dict[str, Any], *, actor_id: str = "") -> dict[str, Any]:
        return {
            "ok": False,
            "domain_id": self._manifest.id,
            "dataset": dataset,
            "message": "Domain dataset is read-only or unavailable.",
        }

    def list_events(
        self,
        *,
        entity_type: str = "",
        entity_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return []

    def list_work_items(
        self,
        *,
        state_type: str = "",
        status: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return []

    def execute_action(self, action: str, payload: dict[str, Any], *, actor_id: str = "") -> dict[str, Any]:
        return {
            "ok": False,
            "domain_id": self._manifest.id,
            "action": action,
            "message": "Unknown or unavailable domain action.",
        }

    def apply_state(self, state: dict[str, Any]) -> None:
        self.installed = bool(state.get("installed", self.installed))
        self.enabled = bool(state.get("enabled", self.enabled))
        self.configured = bool(state.get("configured", self.configured))
        metadata = dict(state.get("metadata") or {})
        config = dict(state.get("config") or {})
        self.metadata.update(metadata)
        if config:
            self.metadata["config"] = config


class ModuleStateStore:
    """SQLite persistence for enterprise module lifecycle state."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def load(self, module_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT installed, enabled, configured, metadata_json, config_json "
            "FROM enterprise_module_state WHERE module_id=?",
            (module_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except Exception:
            metadata = {}
        try:
            config = json.loads(row["config_json"] or "{}")
        except Exception:
            config = {}
        return {
            "installed": bool(row["installed"]),
            "enabled": bool(row["enabled"]),
            "configured": bool(row["configured"]),
            "metadata": metadata,
            "config": config,
        }

    def save(self, runtime: StaticDomainRuntime) -> None:
        status = runtime.status()
        config = runtime.config()
        metadata = dict(status.get("metadata") or {})
        metadata.pop("config", None)
        self.conn.execute(
            "INSERT OR REPLACE INTO enterprise_module_state "
            "(module_id, installed, enabled, configured, metadata_json, config_json, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                runtime.manifest().id,
                1 if status.get("installed") else 0,
                1 if status.get("enabled") else 0,
                1 if status.get("configured") else 0,
                json.dumps(metadata, ensure_ascii=False),
                json.dumps(config, ensure_ascii=False),
                int(time.time() * 1000),
            ),
        )
        self.conn.commit()
