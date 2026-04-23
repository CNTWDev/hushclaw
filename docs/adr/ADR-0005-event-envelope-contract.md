# ADR-0005: Event Envelope Contract

## Status
Accepted

## Context
HushClaw is moving toward a session-first architecture where durable state lives in
the event log and the harness can be rebuilt from storage. That direction is
correct, but the current implementation still leaves too much room for
reconstruction by inference:

- some projections derive turn pairs from session-level timestamps instead of
  consuming explicit event references
- event completion can overwrite the original payload, weakening replay and audit
- artifacts may be referenced in payload JSON but not in the indexed event fields
- event ordering currently depends on timestamps alone, which is not stable enough
  for fully reliable replay

To keep modules decoupled, the event envelope must be treated as a product
contract rather than an implementation detail.

## Decision
Adopt the following event contract rules.

### 1. Events are immutable facts

- `events` is the durable source of truth for runtime facts.
- An event records what happened, not a reconstructed summary of what likely
  happened.
- Completion and failure flows may enrich an event, but they must preserve the
  original request payload instead of replacing it.

### 2. Every runtime event must be self-describing

The envelope must carry enough identity to let downstream systems consume the
event without scanning unrelated session history.

Required identifiers by scope:

- `event_id`: unique event identity
- `session_id`: durable conversation boundary
- `thread_id`: durable execution branch boundary
- `run_id`: one execution of a thread
- `step_id`: one model/tool step inside a run

Identifier rules:

- `session_id` is required for every persisted runtime event
- `thread_id` is required for every event emitted after thread creation
- `run_id` is required for every event emitted inside `event_stream`
- `step_id` is required for tool/model sub-steps that may need replay,
  deduplication, or audit

### 3. Events must reference persisted artifacts explicitly

When an event depends on persisted rows or files, the envelope must carry stable
references instead of forcing consumers to rediscover them.

Examples:

- `user_message_received`: include `user_turn_id` once the turn is persisted
- `assistant_message_emitted`: include `assistant_turn_id` and, when needed,
  `user_turn_id`
- `tool_call_requested` / `tool_call_completed`: include `tool_name`,
  normalized `input`, `call_id`, and `artifact_id` when a tool result was
  externalized
- artifact-backed events must store `artifact_id` both in payload and in the
  indexed `artifact_id` column

### 4. Event ordering must be total for replay consumers

- Timestamps alone are not a sufficient replay cursor.
- Projections and replay consumers must order by a stable total key such as
  `(ts, event_id)`.
- Cursor state must retain enough information to resume without skipping
  same-timestamp events.

### 5. Projections must consume event references, not session-wide heuristics

- Projection workers may read referenced rows by ID.
- Projection workers must not reconstruct meaning by scanning an entire session
  using `ts <= event_ts` or similar coarse queries when a more specific
  reference is available.
- If a projection needs additional data, the event schema should be extended
  rather than teaching the projection to guess.

### 6. Event families should stay narrow and predictable

Recommended runtime families:

- trigger events: `user_message_received`, `external_trigger_received`
- run lifecycle: `run_started`, `run_completed`, `run_failed`
- step lifecycle: `model_invoked`, `tool_call_requested`,
  `tool_call_completed`, `tool_call_failed`
- output events: `assistant_message_emitted`, `artifact_created`
- orchestration events: `thread_created`, `handoff_requested`,
  `handoff_completed`
- transport-only replay events should be clearly namespaced, for example `ws:*`

## Consequences
Positive:

- projections can be rebuilt deterministically
- multi-agent thread history becomes inspectable without special-case logic
- retention, audit, and replay can follow explicit references instead of
  reverse-engineering state
- adding new derived systems becomes easier because the event contract is stable

Trade-offs:

- event payloads become slightly more verbose
- emitters must persist referenced rows before writing some events
- schema evolution needs more discipline because downstream consumers rely on it

## Rollout Guidance
1. Change `EventStore.complete()` and `fail()` to merge into the existing payload.
2. Persist `artifact_id` into the indexed event column whenever a tool result is
   externalized.
3. Add `user_turn_id`, `assistant_turn_id`, and `step_id` to the relevant event
   payloads.
4. Move projection cursors from `last_ts` to a stable `(last_ts, last_event_id)`
   resume point, or introduce an explicit monotonic sequence later.
5. Update projections to consume explicit turn and artifact references instead of
   session-wide timestamp scans.
