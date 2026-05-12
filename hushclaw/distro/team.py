"""Team distribution — Knowledge Hub deployment mode."""
from __future__ import annotations

import os
from typing import Any, TYPE_CHECKING

from hushclaw.distro.base import AgentProfile, DistroManifest, PolicyRuleSet
from hushclaw.runtime.principal import RuntimePrincipal, SINGLE_USER_PRINCIPAL

if TYPE_CHECKING:
    from hushclaw.os_api import AgentOSService

_HUB_TOKEN_ENV = "HUSHCLAW_HUB_TOKEN"


class TeamDistro:
    """Hub deployment mode. Exposes /knowledge/* HTTP API for federated personal instances.

    Deploy with: hushclaw serve --distro team
    Write access is gated by the HUSHCLAW_HUB_TOKEN env var (optional; omit for trusted LAN).
    """

    _manifest = DistroManifest(
        id="team",
        name="HushClaw Knowledge Hub",
        description="Shared knowledge hub for federated personal HushClaw instances.",
        storage_profile="local_sqlite",
        policy_profile="workspace_rbac",
        scope_support=["global", "workspace"],
        capabilities=["shared_knowledge_hub"],
    )

    def manifest(self) -> DistroManifest:
        return self._manifest

    # ── Assembly-time ─────────────────────────────────────────────────────

    def agent_profile(self) -> AgentProfile:
        return AgentProfile()

    def policy_rules(self) -> PolicyRuleSet:
        return PolicyRuleSet()

    def runtime_principal(self, **kwargs: Any) -> RuntimePrincipal:
        workspace_id = str(kwargs.get("workspace_id") or "")
        source_channel = str(kwargs.get("source_channel") or "local")
        if workspace_id or source_channel != "local":
            return RuntimePrincipal(
                workspace_id=workspace_id,
                source_channel=source_channel,
            )
        return SINGLE_USER_PRINCIPAL

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def on_startup(self, os_api: "AgentOSService") -> None:
        from hushclaw.distro._hub_routes import register_hub_routes
        token = os.environ.get(_HUB_TOKEN_ENV, "")
        register_hub_routes(os_api, token)

    async def on_shutdown(self) -> None:
        pass
