"""Domain registry for AgentOS business capability packages."""
from __future__ import annotations

from typing import Any

from hushclaw.domains.base import DomainRuntime


class DomainRegistry:
    """In-memory v1 registry for domain runtimes.

    The registry is deliberately generic: it stores domain manifests and runtime
    adapters, but it never interprets business-specific entities.
    """

    def __init__(self, domains: list[DomainRuntime] | None = None) -> None:
        self._domains: dict[str, DomainRuntime] = {}
        for domain in domains or []:
            self.register(domain)

    def register(self, domain: DomainRuntime) -> None:
        manifest = domain.manifest()
        self._domains[manifest.id] = domain

    def get(self, domain_id: str) -> DomainRuntime | None:
        return self._domains.get(domain_id)

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "manifest": domain.manifest().to_dict(),
                "status": domain.status(),
            }
            for domain in sorted(self._domains.values(), key=lambda item: item.manifest().id)
        ]

    def manifest(self, domain_id: str) -> dict[str, Any]:
        domain = self.get(domain_id)
        return domain.manifest().to_dict() if domain else {}

    def status(self, domain_id: str) -> dict[str, Any]:
        domain = self.get(domain_id)
        return domain.status() if domain else {}

    def install(self, domain_id: str, *, scope: str = "org") -> dict[str, Any]:
        domain = self.get(domain_id)
        if domain is None:
            return {"ok": False, "domain_id": domain_id, "message": f"Unknown domain: {domain_id}"}
        return domain.install(scope=scope)

    def enable(self, domain_id: str, *, scope: str = "org") -> dict[str, Any]:
        domain = self.get(domain_id)
        if domain is None:
            return {"ok": False, "domain_id": domain_id, "message": f"Unknown domain: {domain_id}"}
        return domain.enable(scope=scope)

    def disable(self, domain_id: str, *, scope: str = "org") -> dict[str, Any]:
        domain = self.get(domain_id)
        if domain is None:
            return {"ok": False, "domain_id": domain_id, "message": f"Unknown domain: {domain_id}"}
        return domain.disable(scope=scope)
