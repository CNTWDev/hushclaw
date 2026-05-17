"""Enterprise distribution — org-scoped Agent OS platform mode."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from hushclaw.distro.base import AgentProfile, DistroManifest, PolicyRuleSet
from hushclaw.domains.base import ModuleStateStore
from hushclaw.domains import DomainRegistry
from hushclaw.enterprise import EnterpriseDirectory, EnterpriseDirectoryStore
from hushclaw.prompt_blocks import PromptBlock
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
        self._gateway: Any | None = None
        self._tool_registry: Any | None = None

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
        self._tool_registry = registry
        for domain in self.domain_registry.runtimes():
            status = domain.status()
            manifest = domain.manifest()
            if not status.get("enabled") or manifest.module_type == "foundation":
                continue
            for fn in domain.tools():
                registry.register(fn)

    def register_domain_agents(self, gateway: Any) -> None:
        self._gateway = gateway
        for domain in self.domain_registry.runtimes():
            status = domain.status()
            manifest = domain.manifest()
            if not status.get("enabled") or manifest.module_type == "foundation":
                continue
            for definition in domain.agents():
                register = getattr(gateway, "register_domain_agent", None)
                if register is not None:
                    register({**definition, "domain_id": definition.get("domain_id") or manifest.id})

    def sync_domain_agents(self, domain_id: str) -> None:
        if self._gateway is None:
            return
        domain = self.domain_registry.get(domain_id)
        if domain is None:
            return
        if not domain.status().get("enabled"):
            unregister = getattr(self._gateway, "unregister_domain_agents", None)
            if unregister is not None:
                unregister(domain_id)
            return
        for definition in domain.agents():
            register = getattr(self._gateway, "register_domain_agent", None)
            if register is not None:
                register({**definition, "domain_id": definition.get("domain_id") or domain_id})

    def sync_domain_tools(self, domain_id: str) -> None:
        if self._tool_registry is None:
            return
        domain = self.domain_registry.get(domain_id)
        if domain is None or not domain.status().get("enabled"):
            return
        if domain.manifest().module_type == "foundation":
            return
        for fn in domain.tools():
            self._tool_registry.register(fn)

    def manifest(self) -> DistroManifest:
        return self._manifest

    def agent_profile(self) -> AgentProfile:
        return AgentProfile()

    def policy_rules(self) -> PolicyRuleSet:
        def _can_call_tool(tool_name: str, principal: RuntimePrincipal) -> bool:
            domain_id = tool_name.split(".", 1)[0] if "." in tool_name else ""
            if domain_id and self.domain_registry.get(domain_id):
                if not self.domain_registry.status(domain_id).get("enabled"):
                    return False
                if "owner" in principal.roles:
                    return True
                agent_meta = {}
                if self._gateway is not None:
                    agent_meta = getattr(self._gateway, "_agent_meta", {}).get(principal.principal_id, {}) or {}
                if agent_meta.get("owner_type") == "domain" and agent_meta.get("domain_id") == domain_id:
                    return True
                return self.directory.can_use_domain(principal.principal_id, domain_id, principal.roles)
            # v1 scope: built-in tools (recall, write_file, shell_exec, fetch_url, …)
            # are unrestricted for all principals. Pending v2 RBAC expansion.
            return True

        return PolicyRuleSet(can_call_tool=_can_call_tool)

    def prompt_blocks(self) -> list[PromptBlock]:
        blocks = [
            PromptBlock(
                id="enterprise.org_boundary",
                owner="distro",
                tier="stable",
                priority=20,
                title="Enterprise Org Boundary",
                content=(
                    "## Enterprise Boundary\n"
                    "You are running inside HushClaw Enterprise. Treat organization, "
                    "workspace, domain, and principal boundaries as policy boundaries. "
                    "Use only enabled domain capabilities for domain-specific work. "
                    "Do not assume access to a business domain unless the active "
                    "principal and organization have that domain enabled."
                ),
            )
        ]
        for domain in self.domain_registry.runtimes():
            status = domain.status()
            if not status.get("enabled"):
                continue
            getter = getattr(domain, "prompt_blocks", None)
            if getter is not None:
                blocks.extend(getter())
        return blocks

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
