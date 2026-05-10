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

        Args:
            agent: Already-initialised Agent instance.

        Returns:
            (gateway, os_api) — caller passes these to HushClawServer.
        """
        from hushclaw.gateway import Gateway
        from hushclaw.os_api import AgentOSService

        self._distro.configure_agent(agent.config)
        gateway = Gateway(agent.config, agent)
        self._distro.configure_gateway(gateway)
        return gateway, AgentOSService(gateway=gateway, distro=self._distro)
