"""Distro contract: DistroManifest + DistroAdapter protocol."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class DistroManifest:
    """Declares the identity and capability profile of a distribution."""

    id: str
    name: str
    description: str
    storage_profile: str        # "local_sqlite" | "postgres"
    policy_profile: str         # "personal_owner" | "workspace_rbac" | "org_rbac"
    default_tools: list[str] = field(default_factory=list)
    default_connectors: list[str] = field(default_factory=list)
    web_asset_dir: str | None = None
    scope_support: list[str] = field(default_factory=lambda: ["personal"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "storage_profile": self.storage_profile,
            "policy_profile": self.policy_profile,
            "scope_support": list(self.scope_support),
        }


class DistroAdapter(Protocol):
    """Interface a distribution must implement.

    Distros configure kernel components at assembly time but never own them.
    """

    def manifest(self) -> DistroManifest: ...

    def configure_agent(self, config: Any) -> None:
        """Mutate config before Agent is used. No-op for most distros."""
        ...

    def configure_gateway(self, gateway: Any) -> None:
        """Install distro-level hooks after Gateway is created. No-op for most distros."""
        ...

    def runtime_principal(self, **kwargs: Any) -> Any:
        """Construct the default RuntimePrincipal for this distro."""
        ...
