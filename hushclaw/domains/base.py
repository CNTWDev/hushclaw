"""Domain runtime contract.

AgentOS knows this contract only. Concrete business semantics such as CRM leads
or HR candidates must live in domain packages, never in the kernel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class DomainManifest:
    id: str
    name: str
    description: str = ""
    capabilities: tuple[str, ...] = ()
    entity_types: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()
    ui_entries: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    status: str = "available"  # available | planned | disabled
    category: str = "business"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "entity_types": list(self.entity_types),
            "tools": list(self.tools),
            "agents": list(self.agents),
            "ui_entries": list(self.ui_entries),
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


@dataclass(slots=True)
class StaticDomainRuntime:
    """Read-only placeholder domain used to establish the enterprise substrate."""

    _manifest: DomainManifest
    configured: bool = False
    enabled: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def manifest(self) -> DomainManifest:
        return self._manifest

    def install(self, scope: str = "org") -> dict[str, Any]:
        self.metadata["scope"] = scope
        return {"ok": True, "domain_id": self._manifest.id, "message": "Domain module installed.", "scope": scope}

    def enable(self, scope: str = "org") -> dict[str, Any]:
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
            "installed": True,
            "enabled": self.enabled,
            "configured": self.configured,
            "status": self._manifest.status,
            "metadata": dict(self.metadata),
        }
