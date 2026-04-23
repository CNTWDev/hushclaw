# ADR-0006: Module Ownership & Lifecycle

## Status
Accepted

## Context

Five background modules now run alongside `AgentLoop`: `ProjectionWorker`, `RetentionExecutor`,
`SandboxManager`, `AgentLoop` itself (pooled by `AgentPool`), and `EventStore`. Without a
single authoritative ownership table, shutdown, exception handling, and resource cleanup are
scattered and incomplete:

- `ProjectionWorker` and `RetentionExecutor` are started lazily but never stopped.
- `SandboxManager.close()` is not called on exception paths for one-shot loops (CLI, `Agent.chat()`).
- `AgentLoop` pooled sessions are GC'd correctly via TTL, but ephemeral sessions created outside
  the pool have no guaranteed cleanup path.
- `EventStore` is effectively a property of `MemoryStore` and shares its lifetime, but this was
  implicit.

## Decision

### Ownership table

| Module | Owner | Start | Stop | Constraints |
|--------|-------|-------|------|-------------|
| `EventStore` | `MemoryStore` | `MemoryStore.__init__` | `MemoryStore.close()` | append-only writes; projections may not UPDATE/DELETE events |
| `ProjectionWorker` | `Agent` | `Agent.new_loop()` first call (lazy, idempotent) | `Agent.close()` | Only reads events table; dispatches to `ContextEngine.after_turn()`; never accesses loop internals |
| `RetentionExecutor` | `Agent` | `Agent.new_loop()` first call (lazy, idempotent) | `Agent.close()` | Only deletes from events/turns/artifacts per policy; never writes projections or events |
| `SandboxManager` | `AgentLoop` | `AgentLoop.__init__` | `AgentLoop.aclose()` | `AgentLoop` is the **sole owner**; gateway/pool may only request actions, not hold a reference |
| `AgentLoop` (pooled) | `AgentPool` | `_get_or_create_loop()` | `_gc_stale_sessions()` via TTL (async task) | `aclose()` scheduled as asyncio task on GC; pool session sandbox stays alive between runs |
| `AgentLoop` (ephemeral) | caller (`Agent.chat`, etc.) | `agent.new_loop()` | **finally block in caller** | One-shot loops must `await loop.aclose()` in a try/finally; no TTL GC applies |

### Allowed side-effects per module

| Module | May write to | May read from | Must not |
|--------|-------------|--------------|---------|
| `AgentLoop` (Harness) | `events`, `turns` | `events`, `turns`, `notes`, `sessions` | Write `projections`, `belief_models`, `reflections` |
| `ProjectionWorker` | `projections` (cursor only); `notes`, `belief_models` via `after_turn` | `events`, `turns` | Write `events`; access loop memory state |
| `RetentionExecutor` | DELETE `events`, `turns`, `artifacts` | `security_policies` | Write anything else |
| `SandboxManager` | — (runtime resource only) | — | Hold state that outlives `AgentLoop.aclose()` |
| `EventStore` | `events` (INSERT, UPDATE status/artifact_id only) | `events` | DELETE rows; accessed by projections |

### Ephemeral loop cleanup rule

Any code path that calls `agent.new_loop()` and does **not** add the result to a persistent
pool must guarantee cleanup via `try/finally`:

```python
loop = agent.new_loop(session_id)
try:
    result = await loop.run(message)
finally:
    await loop.aclose()
```

`Agent.chat()` and `Agent.chat_stream()` are the primary examples.

### Pooled loop cleanup rule

`AgentPool._gc_stale_sessions()` is the only code path that removes pooled loops. It already
schedules `asyncio.create_task(loop.aclose())`. No other code path should call `aclose()` on a
loop that remains in the pool, as the sandbox must stay alive for the next run.

For ephemeral loops (session_id absent from the pool at the time of cleanup), `AgentPool.event_stream()`
should schedule `asyncio.create_task(loop.aclose())` in its `finally` block.

### Agent.close() contract

`Agent.close()` must stop `ProjectionWorker` and `RetentionExecutor` before closing `MemoryStore`.
The stop is best-effort (errors swallowed) to avoid masking the primary shutdown reason.

## Consequences

Positive:
- Every module has exactly one owner responsible for its full lifecycle.
- Resource leaks on exception paths are addressed at the framework level, not per-call-site.
- Adding a new background module has a clear template: lazy start in `Agent.new_loop()`,
  stop in `Agent.close()`, write constraints in this ADR.
- Independent iteration is possible: each module's internal implementation can change without
  touching other modules, as long as the ownership and write-permission rules are observed.

Trade-offs:
- `Agent.close()` becomes async-aware (needs to stop async tasks); the sync `close()` method
  must handle the event-loop-or-not case carefully.
- Ephemeral loops require explicit `try/finally` discipline in every caller.
