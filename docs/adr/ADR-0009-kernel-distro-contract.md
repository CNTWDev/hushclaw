# ADR-0009 — Kernel-Distro Interface Contract

**Status:** Accepted  
**Date:** 2026-05-10  
**Supersedes:** ADR-0008 (partial — refines DistroAdapter contract)

---

## Context

ADR-0008 introduced the Distro concept and `DistroRuntime.assemble()`. However, the
`DistroAdapter` Protocol had all parameters typed as `Any`, and `PersonalDistro` implemented
every method as a no-op. The contract was a structural placeholder with no semantic constraint.

Two related problems:

1. `configure_agent(config: Any)` and `configure_gateway(gateway: Any)` are dangerous
   black-box interfaces — a distro could mutate anything inside Config or Gateway, breaking
   kernel invariants silently.

2. The architecture document proposed that distros implement their own storage backends
   (`storage_backend()`, `memory_port()`) — this inverts the correct ownership: storage
   is a kernel concern, not a distro concern.

---

## Decision

### What the Kernel Is

The kernel is the **minimal LLM OS** — a fixed, non-replaceable execution core:

```
AgentLoop           ReAct execution engine (distros cannot replace)
LLMProvider         Model abstraction (distros cannot replace)
MemoryStore         Persistence (internal; distros declare profile, kernel routes)
ToolRegistry        Tool registration (internal; distros influence via AgentProfile)
ContextEngine       Context assembly and compaction (internal)
PolicyGate          Safety gate (always in tool call path; distros may add rules)
AuditLog            Audit envelope (always emitted; distros may add sinks)
RuntimePrincipal    Identity (distros provide factory, kernel injects)
```

The kernel does **not** expose MemoryStore construction, LLMProvider implementation,
or ToolRegistry internals to distros. These are internal implementation details.

### The Three Distro Extension Surfaces

A distro may influence the kernel through exactly three surfaces:

```
1. Skill/Agent Profile    declare which skills are enabled, which agents are pre-configured
2. Policy Rule Set        inject RBAC rules into PolicyGate (personal = empty = permissive)
3. Lifecycle Hooks        on_startup / on_shutdown for distro-owned services (connection pools, etc.)
```

Storage is **not** a distro extension surface. The distro declares
`manifest.storage_profile = "local_sqlite" | "postgres"` and the kernel selects the
implementation internally. A team distro writing `storage_profile = "postgres"` is
sufficient — it never implements a storage adapter.

### What Replace the Dangerous Interfaces

`configure_agent(config: Any)` → replaced by `agent_profile() -> AgentProfile`  
`configure_gateway(gateway: Any)` → replaced by `policy_rules() -> PolicyRuleSet`

Both old methods gave distros unrestricted write access to kernel internals.
The new interfaces are narrowly scoped: `AgentProfile` covers only tool lists and
skill directories; `PolicyRuleSet` covers only policy predicate injection.

### The New DistroAdapter Contract

```python
class DistroAdapter(Protocol):
    def manifest(self) -> DistroManifest: ...

    # Assembly-time (synchronous)
    def agent_profile(self) -> AgentProfile:
        """Declare default skill directories and tool enable/disable lists."""
        ...

    def policy_rules(self) -> PolicyRuleSet:
        """Inject RBAC predicates into PolicyGate. Empty = permissive (personal default)."""
        ...

    def runtime_principal(self, **kwargs: Any) -> RuntimePrincipal:
        """Construct RuntimePrincipal for a request context."""
        ...

    # Lifecycle (asynchronous)
    async def on_startup(self, os_api: AgentOSService) -> None:
        """Called after kernel is assembled, before the server accepts connections."""
        ...

    async def on_shutdown(self) -> None:
        """Called on graceful shutdown."""
        ...
```

### Kernel Invariants (unchanged from ADR-0008)

1. Personal mode works with no login, no network, no cloud broker.
2. AgentLoop, ContextEngine, ToolRegistry are never modified by a distro.
3. PolicyGate is always in the tool call path; distros may **add** rules but not remove it.
4. AuditEvent envelope is always emitted; distros may add sinks but not suppress events.
5. MemoryPort scope isolation is enforced; distros choose the profile, not the boundary.

---

## Supporting Types

```python
@dataclass
class AgentProfile:
    """Distro-declared default agent behavior. Kernel reads this at assembly."""
    default_skill_dirs: list[Path] = field(default_factory=list)
    enabled_tools: list[str] = field(default_factory=list)   # empty = all enabled
    disabled_tools: list[str] = field(default_factory=list)  # explicit deny list
    default_agents: list[dict] = field(default_factory=list) # pre-configured agent defs

@dataclass
class PolicyRuleSet:
    """Distro-injected predicates for PolicyGate. All fields optional; None = permissive."""
    can_call_tool: Callable[[str, RuntimePrincipal], bool] | None = None
    can_read_memory: Callable[[str, RuntimePrincipal], bool] | None = None
    can_use_connector: Callable[[str, RuntimePrincipal], bool] | None = None
```

---

## How DistroRuntime Uses the Contract

```
DistroRuntime.build(config/project_dir):
  1. manifest = distro.manifest()
     → validate manifest.storage_profile before Agent creation
     → unsupported profiles fail with a clear kernel adapter error

  2. profile = distro.agent_profile()
     → apply profile.enabled_tools / disabled_tools to agent.config
     → register profile.default_skill_dirs with SkillRegistry

  3. agent = Agent(config)
     → kernel owns MemoryStore, providers, ToolRegistry, ContextEngine

  4. rules = distro.policy_rules()
     → gateway.install_policy_rules(rules)
     → PolicyGate stores predicates, calls them before hard-coded checks

  5. return RuntimeBundle(agent, gateway, AgentOSService(gateway, distro))

HushClawServer.start():
  6. await distro.on_startup(os_api)
  7. ... run server ...
  8. await distro.on_shutdown()
```

`DistroRuntime.assemble(agent)` remains as a compatibility path for tests and
embedding callers that already created an `Agent`; product shells should prefer
`build()` so distro metadata can participate before kernel resources are created.

---

## Future Distro Example (Team Distro — not implemented)

```python
class TeamDistro:
    def manifest(self) -> DistroManifest:
        return DistroManifest(
            id="team",
            storage_profile="postgres",   # kernel picks PostgresMemoryStore
            policy_profile="workspace_rbac",
            capabilities=["multi_tenant", "shared_workspace"],
            ...
        )

    def policy_rules(self) -> PolicyRuleSet:
        return PolicyRuleSet(
            can_call_tool=lambda tool, principal: principal.workspace_id != "",
            can_read_memory=lambda scope, principal: scope.startswith(principal.workspace_id),
        )

    async def on_startup(self, os_api: AgentOSService) -> None:
        await self._pg_pool.connect()   # initialize Postgres connection pool
        await self._cache.warm(os_api)  # preload workspace metadata

    async def on_shutdown(self) -> None:
        await self._pg_pool.close()
```

The team distro does **not** implement `memory_port()` — it simply declares
`storage_profile="postgres"` and the kernel will route to `PostgresMemoryStore` when
that implementation exists.

---

## Consequences

**Positive**
- DistroAdapter has no `Any` parameters (except `runtime_principal`'s `**kwargs`).
- Distros cannot accidentally break kernel invariants through configure_* black boxes.
- `storage_profile` stays the authoritative selector; no distro-side adapter proliferation.
- `on_startup` / `on_shutdown` give team/enterprise distros a clean place for their init code.
- PersonalDistro returns empty values from all new methods — zero behavior change.

**Negative / Trade-offs**
- Two existing methods (`configure_agent`, `configure_gateway`) are removed — any code
  calling them (currently only PersonalDistro with no-ops) must be updated.
- Distros that need unusual kernel customization beyond AgentProfile + PolicyRuleSet
  have no escape hatch in v1. That is intentional: the seam should be narrow.

---

## Open Questions

- **PostgresMemoryStore**: when `storage_profile="postgres"` is declared, what triggers
  the kernel to build it? `DistroRuntime.build()` now validates the profile before
  `Agent` creation and raises a clear unsupported-adapter error. The actual
  `PostgresMemoryStore` adapter and selection table are deferred to the team distro
  implementation milestone.
- **default_agents**: AgentProfile carries `default_agents` but Gateway.create_agent()
  is async — on_startup() is the right place to create them, not assemble(). Document
  this pattern when TeamDistro is implemented.
