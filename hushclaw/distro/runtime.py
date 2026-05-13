"""DistroRuntime — resolves and assembles a distribution profile."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from hushclaw.distro.base import DistroManifest
    from hushclaw.agent import Agent
    from hushclaw.config import Config
    from hushclaw.gateway import Gateway
    from hushclaw.os_api import AgentOSService


@dataclass(slots=True)
class RuntimeBundle:
    """Fully assembled runtime for product shells."""

    agent: "Agent"
    gateway: "Gateway"
    os_api: "AgentOSService"
    distro: Any

    def close(self) -> None:
        self.gateway.close()


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

    def build(
        self,
        *,
        config: "Config | None" = None,
        project_dir: "Path | None" = None,
    ) -> RuntimeBundle:
        """Build Agent, Gateway, and AgentOSService with distro profile applied first.

        Product shells should prefer this entrypoint. It lets distro metadata
        participate before Agent creates storage, providers, and registries.
        """
        from hushclaw.agent import Agent
        from hushclaw.config import load_config

        manifest = self.manifest()
        if manifest.storage_profile != "local_sqlite":
            raise ValueError(
                f"Distro {manifest.id!r} requires storage_profile={manifest.storage_profile!r}, "
                "but no kernel storage adapter is registered for that profile."
            )

        cfg = config or load_config(project_dir)
        self._apply_agent_profile_to_config(cfg)
        agent = Agent(config=cfg)
        gateway, os_api = self.assemble(agent, profile_already_applied=True)
        return RuntimeBundle(agent=agent, gateway=gateway, os_api=os_api, distro=self._distro)

    def assemble(self, agent: Any, *, profile_already_applied: bool = False) -> tuple[Any, Any]:
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

        manifest = self.manifest()
        if manifest.storage_profile != "local_sqlite":
            raise ValueError(
                f"Distro {manifest.id!r} requires storage_profile={manifest.storage_profile!r}, "
                "but no kernel storage adapter is registered for that profile."
            )

        # 1. Apply AgentProfile for legacy callers that already created Agent.
        if not profile_already_applied:
            self._apply_agent_profile_to_config(agent.config)
            if hasattr(agent, "reload_runtime"):
                agent.reload_runtime(agent.config)

        # 2. Build Gateway
        gateway = Gateway(agent.config, agent)

        # 3. Inject PolicyRuleSet predicates (safe narrow interface — no gateway exposure)
        rules = self._distro.policy_rules()
        if rules.can_call_tool or rules.can_read_memory or rules.can_use_connector:
            gateway.install_policy_rules(rules)

        os_api = AgentOSService(gateway=gateway, distro=self._distro)
        gateway._os_api = os_api
        return gateway, os_api

    def _apply_agent_profile_to_config(self, config: Any) -> None:
        """Apply the narrow distro profile to Config before kernel assembly."""
        profile = self._distro.agent_profile()
        if profile.enabled_tools:
            config.tools.enabled = list(profile.enabled_tools)
        if profile.disabled_tools:
            current = list(config.tools.enabled or [])
            config.tools.enabled = [t for t in current if t not in profile.disabled_tools]
        # ``default_skill_dirs`` are intentionally deferred until the skill
        # loader has a first-class multi-dir system setting. Keeping this here
        # avoids ad-hoc mutation of config.tools.skill_dir into a lossy single
        # path and preserves existing personal behavior.
