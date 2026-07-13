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
- Interactive product shells, background schedulers, and channel connectors
  must enter execution through `AgentOSService.stream_message()` or
  `AgentOSService.execute_message()`. Direct calls to Gateway execution methods
  from those layers are forbidden and enforced by architecture tests.
- External conversations use `ConversationAddress` and persistent
  `ConversationBinding` records; internal `session_id` values are not platform
  conversation identifiers.
- Outbound channel replies pass through `AgentOSService.deliver_message()`.
  Delivery intent and terminal status are recorded in the durable outbox before
  and after a platform adapter is invoked.
- Streamed runtime events use the canonical `AgentOSEvent` envelope. Existing
  wire fields remain flat for clients, with stable schema, source, session,
  thread, run, step, event, and timestamp metadata added by the OS boundary.

## Migration policy

- Once a product shell moves to an Agent OS boundary, its direct Gateway path is
  removed in the same change. There is no permanent compatibility fallback.
- Database evolution is additive and versioned. Existing databases are backed
  up before a version upgrade; migrations are idempotent and do not rewrite
  existing session or memory rows.
- Deprecated execution paths are guarded by tests so later maintenance cannot
  accidentally reintroduce them.

## Consequences

This does not extract a new kernel package yet. It creates stable seams so future
work on app connectors, memory, policy, audit, and team overlays can move behind
interfaces without disrupting the personal/local runtime.
