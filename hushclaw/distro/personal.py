"""Personal distribution — wraps current single-user local-first behavior."""
from __future__ import annotations

from typing import Any

from hushclaw.distro.base import DistroManifest
from hushclaw.runtime.principal import RuntimePrincipal, SINGLE_USER_PRINCIPAL


class PersonalDistro:
    """Default local-first personal distribution.

    Behavior is identical to pre-distro HushClaw. configure_agent() and
    configure_gateway() are no-ops because the personal profile is the baseline
    that all config defaults already target.
    """

    _manifest = DistroManifest(
        id="personal",
        name="HushClaw Personal",
        description="Local-first personal AI assistant. Data stays on device.",
        storage_profile="local_sqlite",
        policy_profile="personal_owner",
        scope_support=["personal", "global", "workspace"],
    )

    def manifest(self) -> DistroManifest:
        return self._manifest

    def configure_agent(self, config: Any) -> None:
        pass

    def configure_gateway(self, gateway: Any) -> None:
        pass

    def runtime_principal(self, **kwargs: Any) -> RuntimePrincipal:
        workspace_id = str(kwargs.get("workspace_id") or "")
        source_channel = str(kwargs.get("source_channel") or "local")
        if workspace_id or source_channel != "local":
            return RuntimePrincipal(
                workspace_id=workspace_id,
                source_channel=source_channel,
            )
        return SINGLE_USER_PRINCIPAL
