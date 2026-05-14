"""Enterprise distribution — org-scoped Agent OS platform mode."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from hushclaw.distro.base import AgentProfile, DistroManifest, PolicyRuleSet
from hushclaw.domains.base import ModuleStateStore
from hushclaw.domains import DomainRegistry
from hushclaw.enterprise import EnterpriseDirectory, EnterpriseDirectoryStore
from hushclaw.runtime.principal import RuntimePrincipal
from hushclaw.solutions.enterprise import enterprise_domain_registry

if TYPE_CHECKING:
    from hushclaw.os_api import AgentOSService


class EnterpriseDistro:
    """Enterprise platform profile.

    v1 installs the org directory and domain registry substrate only. Concrete
    business behavior remains outside the AgentOS kernel and arrives via domain
    runtimes owned by the enterprise solution.
    """

    _manifest = DistroManifest(
        id="enterprise",
        name="HushClaw Enterprise",
        description="Org-scoped Agent OS platform for enterprise domains, RBAC, and audit governance.",
        storage_profile="local_sqlite",
        policy_profile="org_rbac",
        web_shell="enterprise_workspace",
        admin_shell="enterprise_admin",
        module_catalog="enterprise",
        scope_support=["personal", "workspace", "org", "domain"],
        capabilities=["org_directory", "domain_runtime", "audit_governance", "enterprise_admin"],
    )

    def __init__(
        self,
        *,
        directory: EnterpriseDirectory | None = None,
        domain_registry: DomainRegistry | None = None,
    ) -> None:
        self.directory = directory or EnterpriseDirectory()
        self.domain_registry = domain_registry or enterprise_domain_registry()
        self._directory_store: EnterpriseDirectoryStore | None = None

    def bind_memory(self, memory: Any) -> None:
        conn = getattr(memory, "conn", None)
        if conn is None:
            return
        self._directory_store = EnterpriseDirectoryStore(conn)
        self.directory = self._directory_store.load()
        self.domain_registry.bind_state_store(ModuleStateStore(conn))
        for domain in self.domain_registry.runtimes():
            bind_memory = getattr(domain, "bind_memory", None)
            if bind_memory is not None:
                bind_memory(memory)

    def persist_directory(self) -> None:
        if self._directory_store is not None:
            self._directory_store.save(self.directory)

    def register_domain_tools(self, registry: Any) -> None:
        for domain in self.domain_registry.runtimes():
            status = domain.status()
            manifest = domain.manifest()
            if not status.get("enabled") or manifest.module_type == "foundation":
                continue
            for fn in domain.tools():
                registry.register(fn)

    def manifest(self) -> DistroManifest:
        return self._manifest

    def agent_profile(self) -> AgentProfile:
        return AgentProfile()

    def policy_rules(self) -> PolicyRuleSet:
        def _can_call_tool(tool_name: str, principal: RuntimePrincipal) -> bool:
            domain_id = tool_name.split(".", 1)[0] if "." in tool_name else ""
            if domain_id and self.domain_registry.get(domain_id):
                return "owner" in principal.roles or "domain-admin" in principal.roles
            # v1 scope: built-in tools (recall, write_file, shell_exec, fetch_url, …)
            # are unrestricted for all principals. Pending v2 RBAC expansion.
            return True

        return PolicyRuleSet(can_call_tool=_can_call_tool)

    def runtime_principal(self, **kwargs: Any) -> RuntimePrincipal:
        principal_id = str(kwargs.get("principal_id") or "local-user")
        org_id = str(kwargs.get("org_id") or self.directory.snapshot().org.id)
        workspace_id = str(kwargs.get("workspace_id") or "")
        roles = tuple(kwargs.get("roles") or ("owner",))
        source_channel = str(kwargs.get("source_channel") or "webui")
        return RuntimePrincipal(
            principal_id=principal_id,
            org_id=org_id,
            workspace_id=workspace_id,
            roles=roles,
            mode="enterprise",
            source_channel=source_channel,
        )

    async def on_startup(self, os_api: "AgentOSService") -> None:
        # TODO: register /enterprise/api/* routes via os_api.register_http_handler
        # once the enterprise admin backend is implemented.
        pass

    async def on_shutdown(self) -> None:
        pass
