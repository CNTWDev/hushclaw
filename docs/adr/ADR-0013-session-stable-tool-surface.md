# ADR-0013: Session-stable tool surface and performance projection

Status: Accepted — 2026-08-31

## Context

HushClaw's registry has grown from a small set of built-ins into a capability
catalog containing browser actions, connectors, installed skill tools, agent
operations, and domain tools. Sending the complete schema catalog on every
model round has three costs:

1. repeated input tokens and provider-side schema processing;
2. higher time-to-first-token, especially for multi-round tool tasks;
3. prompt-cache misses when a skill install or registry reload changes the tool
   definitions during an existing conversation.

Context assembly also performed SQLite/FTS/vector and workspace reads directly
inside the async turn path. A slow recall could therefore stall unrelated
streams sharing the same event loop. Finally, latency existed only as nested
JSON on assistant events, which made regressions difficult to query.

Recent Hermes Agent code reinforced three relevant patterns: keep the
conversation prefix byte-stable, put capability discovery at the edge instead
of growing the core surface, and use memory/disk caches with background refresh
for metadata that may require the network.

## Decision

### 1. Freeze one exact provider surface per session

`ToolSurfaceSnapshot` selects the provider-facing schemas once. The exact JSON
is stored in `session_tool_surfaces` with `INSERT OR IGNORE`; the first complete
snapshot wins. Resuming the session reuses that JSON across loop recreation and
process restart. Registry changes become available in new sessions.

Modes:

- `all`: expose every enabled schema;
- `bridge`: expose eager tools plus `tool_search` and `tool_call`;
- `auto`: use `bridge` only when the estimated complete schema exceeds
  `tools.schema_budget_tokens`.

### 2. Keep the security/runtime path as the narrow waist

`tool_search` returns exact names and input schemas from the active registry.
When the model invokes `tool_call`, `AgentLoop` resolves it into the underlying
tool call before confirmation, policy, audit, timeout, output budgeting, and
mutation verification. Provider history and tool-result identity retain the
`tool_call` wrapper so the submitted conversation stays valid against its
frozen schema set.

The bridge is discovery, not an authorization bypass. `AgentConfig.allowed_tools`
and `PolicyGate` apply to the resolved underlying name.

### 3. Remove blocking recall from the asyncio scheduler

The dynamic context suffix is assembled with `asyncio.to_thread`. Its existing
SQLite connection locking and recall behavior remain unchanged, but concurrent
sessions, WebSocket streaming, and heartbeats no longer wait behind local FTS,
vector scoring, or workspace file reads.

### 4. Project latency without rewriting history

Assistant events remain the source of truth. Schema v5 adds a disposable
`run_metrics` projection populated by SQLite triggers and idempotently backfills
historical assistant events that already contain `perf`. No turns, events,
notes, sessions, or artifacts are deleted or rewritten. Pre-upgrade databases
are backed up by the existing versioned migration path.

## Consequences

- Default browser-enabled local registry benchmark: 72 registered schemas,
  21 provider-visible schemas, estimated 8,191 → 2,659 schema tokens (67.5%).
- A newly installed tool intentionally does not appear directly inside an
  already-started session. If it is part of that session's registry it remains
  discoverable through the frozen bridge; otherwise start a new session.
- Provider latency and external search latency still depend on network/model
  service. `run_metrics` now separates assemble, TTFT, LLM, tool, persistence,
  and total time so further work can target the actual bottleneck.
- Surface construction adds a small one-time cost per new session and a compact
  SQLite row containing schema JSON.

## Rejected alternatives

- Per-turn heuristic tool lists: smaller, but change prompt/tool definitions and
  destroy cache stability.
- Bypass the runtime for bridge calls: faster to implement, but creates a policy
  and audit escape hatch.
- Rewrite old events into a new run model: unnecessary risk; a rebuildable
  projection provides query performance while preserving raw facts.
