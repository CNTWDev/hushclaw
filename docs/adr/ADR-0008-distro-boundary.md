# ADR-0008 — Kernel and Distro Packaging Boundary

**Status:** Accepted  
**Date:** 2026-05-10

---

## Context

HushClaw is evolving toward an "Agent OS Layer": a shared kernel that can support multiple
product distributions (Personal, Enterprise, future IoT). The existing codebase was built
as a single-user personal assistant. Kernel seams (AgentOSService, RuntimePrincipal,
MemoryPort, ExtensionManifest, AuditEvent) have been added, but the startup path still
directly wires `CLI → Agent → Gateway → Server` with no distribution concept.

Without a formal boundary, adding team or enterprise capabilities means either polluting
the personal product or maintaining a fork — both are costly.

---

## Decision

Introduce a **Distro** concept as a thin runtime profile contract, distinct from:
- A UI skin (distros configure kernel behavior, not only appearance)
- A PyPI package split (v1 keeps a single package; physical split is a v2+ concern)
- A fork (all distros share the exact same kernel and its updates)

A distro is **the unit that answers**: "given this deployment context, how should the
kernel components be assembled and which defaults apply?"

### What a Distro Is

```
DistroManifest   — declares id, storage_profile, policy_profile, scope_support
DistroAdapter    — configure_agent(), configure_gateway(), runtime_principal()
DistroRuntime    — registry + assemble(agent) → (Gateway, AgentOSService)
```

### What a Distro Is Not

A distro does not own AgentLoop, ToolRegistry, MemoryPort, Provider adapters, or any
other kernel component. It may configure them through the adapter interface, but it
cannot replace or bypass them.

### Startup path (after this ADR)

```
CLI
└─ DistroRuntime(distro_id="personal")
   └─ distro.configure_agent(config)
   └─ Gateway(config, agent)          ← kernel
   └─ distro.configure_gateway(gateway)
   └─ AgentOSService(gateway, distro) ← OS façade
      └─ HushClawServer               ← transport shell
```

### Kernel invariants (must hold for every distro)

1. Personal mode works with no login, no network, no cloud broker.
2. AgentLoop, ContextEngine, ToolRegistry are never modified by a distro.
3. PolicyGate is always in the tool call path; distros may add rules but not remove it.
4. AuditEvent envelope is always emitted; distros may add sinks but not suppress events.
5. MemoryPort scope isolation is enforced; distros choose the adapter, not the boundary.

---

## Distro Registry (v1)

| ID | Class | Status |
|----|-------|--------|
| `personal` | `PersonalDistro` | Implemented — wraps current behavior |

`team` is intentionally not a distro in the current architecture. Team-oriented
collaboration is treated as a Personal enhancement path, not a third product
shell.

IoT is **not** listed as a distro because it requires a different runtime (non-Python,
<200 ms latency, hardware protocol adapters). IoT is a separate product that may embed
kernel components, not a distribution of the same runtime.

---

## Consequences

**Positive**
- A new distro has one explicit registration point through `DistroRuntime.register()`.
- `hushclaw serve` behavior is unchanged; `--distro personal` is the explicit default.
- AgentOSService can expose `distro_manifest()` so the WebUI knows which profile is active.
- Physical package split becomes straightforward once a non-monorepo distribution need is real.

**Negative / Trade-offs**
- DistroRuntime is a new concept for contributors to learn.
- The `assemble()` method creates Gateway internally; callers must stop creating Gateway
  directly — existing CLI commands need updating.

---

## Physical Package Split (v2+, not in this ADR)

When a real packaging need appears, the packages can be split:

```
hushclaw-kernel     AgentLoop, ToolRegistry, MemoryPort, Provider adapters,
                    RuntimePrincipal, AgentOSService, Extension contract

hushclaw-personal   PersonalDistro, WebUI assets, local SQLite adapter,
                    local secret store, personal CLI defaults

hushclaw-enterprise Optional future package with directory, RBAC/audit defaults,
                    and domain package catalog
```

Trigger condition: a third party wanting to build on the kernel, or deployment
needs that make the monorepo release artifact too costly to maintain.

---

## Open Questions

- **Open-source strategy**: kernel open-source (Apache 2.0) vs source-available is a
  business decision outside this ADR's scope. The distro boundary is equally useful
  in either model.
- **Distro discovery**: `hushclaw distros list` command and HTTP endpoint not yet defined.
