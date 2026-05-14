"""Domain registry for AgentOS business capability packages."""
from __future__ import annotations

from typing import Any

from hushclaw.domains.base import DomainRuntime, ModuleStateStore, StaticDomainRuntime


class DomainRegistry:
    """In-memory v1 registry for domain runtimes.

    The registry is deliberately generic: it stores domain manifests and runtime
    adapters, but it never interprets business-specific entities.
    """

    def __init__(self, domains: list[DomainRuntime] | None = None) -> None:
        self._domains: dict[str, DomainRuntime] = {}
        self._state_store: ModuleStateStore | None = None
        for domain in domains or []:
            self.register(domain)

    def register(self, domain: DomainRuntime) -> None:
        manifest = domain.manifest()
        self._domains[manifest.id] = domain
        if self._state_store is not None:
            self._load_domain_state(domain)

    def bind_state_store(self, state_store: ModuleStateStore) -> None:
        self._state_store = state_store
        for domain in self._domains.values():
            self._load_domain_state(domain)

    def get(self, domain_id: str) -> DomainRuntime | None:
        return self._domains.get(domain_id)

    def runtimes(self) -> list[DomainRuntime]:
        return list(self._domains.values())

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "manifest": domain.manifest().to_dict(),
                "status": domain.status(),
            }
            for domain in sorted(self._domains.values(), key=lambda item: item.manifest().id)
        ]

    def dependency_status(self, domain_id: str) -> dict[str, Any]:
        domain = self.get(domain_id)
        if domain is None:
            return {"ok": False, "domain_id": domain_id, "missing": [], "satisfied": []}
        missing = []
        satisfied = []
        for dep_id in domain.manifest().dependencies:
            dep = self.get(dep_id)
            if dep is not None and dep.status().get("enabled"):
                satisfied.append(dep_id)
            else:
                missing.append(dep_id)
        return {"ok": not missing, "domain_id": domain_id, "missing": missing, "satisfied": satisfied}

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
        deps = self.dependency_status(domain_id)
        if not deps["ok"]:
            return {
                "ok": False,
                "domain_id": domain_id,
                "message": f"Missing dependencies: {', '.join(deps['missing'])}",
                "missing_dependencies": deps["missing"],
            }
        result = domain.install(scope=scope)
        self._save_domain_state(domain)
        return result

    def enable(self, domain_id: str, *, scope: str = "org") -> dict[str, Any]:
        domain = self.get(domain_id)
        if domain is None:
            return {"ok": False, "domain_id": domain_id, "message": f"Unknown domain: {domain_id}"}
        deps = self.dependency_status(domain_id)
        if not deps["ok"]:
            return {
                "ok": False,
                "domain_id": domain_id,
                "message": f"Missing dependencies: {', '.join(deps['missing'])}",
                "missing_dependencies": deps["missing"],
            }
        result = domain.enable(scope=scope)
        self._save_domain_state(domain)
        return result

    def disable(self, domain_id: str, *, scope: str = "org") -> dict[str, Any]:
        domain = self.get(domain_id)
        if domain is None:
            return {"ok": False, "domain_id": domain_id, "message": f"Unknown domain: {domain_id}"}
        result = domain.disable(scope=scope)
        self._save_domain_state(domain)
        return result

    def config(self, domain_id: str) -> dict[str, Any]:
        domain = self.get(domain_id)
        if domain is None:
            return {"ok": False, "domain_id": domain_id, "message": f"Unknown domain: {domain_id}"}
        return {"ok": True, "domain_id": domain_id, "config": domain.config()}

    def update_config(self, domain_id: str, config: dict[str, Any]) -> dict[str, Any]:
        domain = self.get(domain_id)
        if domain is None:
            return {"ok": False, "domain_id": domain_id, "message": f"Unknown domain: {domain_id}"}
        result = domain.update_config(config)
        self._save_domain_state(domain)
        return result

    def _load_domain_state(self, domain: DomainRuntime) -> None:
        if self._state_store is None or not isinstance(domain, StaticDomainRuntime):
            return
        state = self._state_store.load(domain.manifest().id)
        if state is None:
            self._state_store.save(domain)
            return
        domain.apply_state(state)

    def _save_domain_state(self, domain: DomainRuntime) -> None:
        if self._state_store is None or not isinstance(domain, StaticDomainRuntime):
            return
        self._state_store.save(domain)
