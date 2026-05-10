"""Distro contract: DistroManifest, AgentProfile, PolicyRuleSet, DistroAdapter protocol."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from hushclaw.runtime.principal import RuntimePrincipal
    from hushclaw.os_api import AgentOSService


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
    kernel_version_min: str = "0.1"
    capabilities: list[str] = field(default_factory=list)
    # e.g. ["multi_tenant", "audit_retention", "sso", "shared_workspace"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "storage_profile": self.storage_profile,
            "policy_profile": self.policy_profile,
            "scope_support": list(self.scope_support),
            "capabilities": list(self.capabilities),
        }


@dataclass
class AgentProfile:
    """Distro-declared default agent behaviour. Kernel reads this at assembly time.

    empty enabled_tools  → all tools enabled (personal default)
    non-empty            → only listed tools enabled
    disabled_tools       → removed from the effective enabled set
    default_skill_dirs   → additional skill directories loaded at startup
    default_agents       → pre-configured agent definitions (created in on_startup)
    """

    default_skill_dirs: list[Path] = field(default_factory=list)
    enabled_tools: list[str] = field(default_factory=list)
    disabled_tools: list[str] = field(default_factory=list)
    default_agents: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PolicyRuleSet:
    """Distro-injected predicates for PolicyGate.

    All fields are optional callables.  None means permissive (personal default).
    Predicates receive (resource_id, principal) and return True to allow.
    """

    can_call_tool: "Callable[[str, RuntimePrincipal], bool] | None" = None
    can_read_memory: "Callable[[str, RuntimePrincipal], bool] | None" = None
    can_use_connector: "Callable[[str, RuntimePrincipal], bool] | None" = None


class DistroAdapter(Protocol):
    """Interface a distribution must implement.

    Distros declare preferences and inject rules at assembly time;
    they never own or directly mutate kernel components.
    """

    def manifest(self) -> DistroManifest: ...

    # ── Assembly-time (synchronous) ───────────────────────────────────────

    def agent_profile(self) -> AgentProfile:
        """Declare default skill directories and tool enable/disable lists."""
        ...

    def policy_rules(self) -> PolicyRuleSet:
        """Inject RBAC predicates into PolicyGate. Return empty ruleset for permissive."""
        ...

    def runtime_principal(self, **kwargs: Any) -> "RuntimePrincipal":
        """Construct the RuntimePrincipal for a request context."""
        ...

    # ── Lifecycle (asynchronous) ──────────────────────────────────────────

    async def on_startup(self, os_api: "AgentOSService") -> None:
        """Called after kernel is assembled, before the server accepts connections."""
        ...

    async def on_shutdown(self) -> None:
        """Called on graceful shutdown."""
        ...
