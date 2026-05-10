"""DistroRuntime — resolves and assembles a distribution profile."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from hushclaw.distro.base import DistroManifest


class DistroRuntime:
    """Assembles kernel components (Gateway + AgentOSService) for a named distro.

    Usage::

        gateway, os_api = DistroRuntime("personal").assemble(agent)

    The Agent is created by the caller (CLI / test / embedding app). DistroRuntime
    creates Gateway and AgentOSService, giving the distro a chance to configure both
    before the server starts.

    The registry is module-level so distros registered at import time persist across
    DistroRuntime instances. Call ``DistroRuntime.register(MyDistro())`` once at
    module init.
    """

    _registry: dict[str, Any] = {}

    @classmethod
    def register(cls, adapter: Any) -> None:
        """Register a DistroAdapter under its manifest id."""
        manifest = adapter.manifest()
        cls._registry[manifest.id] = adapter

    def __init__(self, distro_id: str = "personal") -> None:
        if distro_id not in self._registry:
            available = ", ".join(sorted(self._registry)) or "(none registered)"
            raise ValueError(
                f"Unknown distro {distro_id!r}. Available: {available}"
            )
        self._distro = self._registry[distro_id]

    def manifest(self) -> "DistroManifest":
        return self._distro.manifest()

    def assemble(self, agent: Any) -> tuple[Any, Any]:
        """Apply distro configuration and return (Gateway, AgentOSService).

        Assembly order:
        1. Read AgentProfile — apply tool enable/disable list to agent.config
        2. Build Gateway
        3. Install PolicyRuleSet predicates into Gateway's PolicyGate
        4. Return (gateway, AgentOSService(gateway, distro))

        Args:
            agent: Already-initialised Agent instance.

        Returns:
            (gateway, os_api) — caller passes these to HushClawServer.
        """
        from hushclaw.gateway import Gateway
        from hushclaw.os_api import AgentOSService

        # 1. Apply AgentProfile (safe narrow interface — no raw config exposure)
        profile = self._distro.agent_profile()
        if profile.enabled_tools:
            agent.config.tools.enabled = list(profile.enabled_tools)
        if profile.disabled_tools:
            current = list(agent.config.tools.enabled or [])
            agent.config.tools.enabled = [t for t in current if t not in profile.disabled_tools]

        # 2. Build Gateway
        gateway = Gateway(agent.config, agent)

        # 3. Inject PolicyRuleSet predicates (safe narrow interface — no gateway exposure)
        rules = self._distro.policy_rules()
        if rules.can_call_tool or rules.can_read_memory or rules.can_use_connector:
            gateway.install_policy_rules(rules)

        return gateway, AgentOSService(gateway=gateway, distro=self._distro)
