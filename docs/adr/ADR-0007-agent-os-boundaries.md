# ADR-0007: Agent OS Boundaries

## Status

Accepted

## Decision

HushClaw/OpenClaw is evolving toward an Agent OS layer. Product shells such as
the WebUI, CLI, channel connectors, personal assistant, or future team workspace
must not own the AI runtime. They should call OS service boundaries that route to
the kernel components.

The kernel must remain runnable locally without a server-side identity broker,
OAuth broker, or enterprise control plane. Governance features are overlays, not
prerequisites for personal mode.

## Boundaries

- Runtime identity is represented by `RuntimePrincipal` and injected with
  `contextvars`. Personal mode defaults to `local-user`.
- Tool execution receives principal/source metadata through `ToolRuntimeContext`
  and emits audit envelopes through the existing event store.
- Memory access has a `MemoryPort` interface with a SQLite adapter around the
  existing `MemoryStore`.
- Extensions share a lifecycle contract: manifest, install, enable, disable,
  status, uninstall. Skills, app connectors, channel connectors, and agents keep
  separate runtimes but can be discovered through one extension registry.
- `AgentOSService` is the service facade Product Shells should migrate toward
  before adding a broader HTTP API surface.

## Consequences

This does not extract a new kernel package yet. It creates stable seams so future
work on app connectors, memory, policy, audit, and team overlays can move behind
interfaces without disrupting the personal/local runtime.
