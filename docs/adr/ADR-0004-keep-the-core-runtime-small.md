# ADR-0004: Keep the Core Runtime Small

## Status
Accepted

## Context
As HushClaw gained memory, hooks, multi-agent routing, browser UI, scheduled tasks, and connectors, the main architectural risk shifted from "missing features" to "accidental weight":

- protocol edges could start leaking into the core runtime
- convenience helpers could become framework-like indirection
- new capabilities could be modeled as extra layers instead of small extensions to existing boundaries
- large files could be split for appearance rather than for a real responsibility boundary

The project goal is not maximal abstraction. The goal is a long-lived agent runtime that stays easy to inspect, cheap to operate, and simple to modify.

## Decision
Adopt a lightweight boundary model:

1. `loop.py` is the core runtime
   - owns turn execution, tool loop, compaction triggering, and persistence flow
   - should stay independent of WebSocket protocol details and UI-specific concerns

2. `context/` is the context lifecycle
   - owns prompt assembly and compaction policy
   - should not absorb server, connector, or orchestration logic

3. `memory/` is durable state and retrieval
   - owns notes, sessions, search, lineage, and working-state persistence
   - should not know about transport protocols

4. `server_impl.py` is the protocol edge
   - owns WebSocket message handling and thin request/response shaping
   - should delegate business logic instead of growing a second runtime

5. `gateway.py` is orchestration
   - owns multi-agent routing, pools, session affinity, and org context
   - should not replace the core loop

6. `agent.py` is composition
   - wires config, provider, memory, tools, skills, hooks, and loops together
   - should remain assembly code, not a hidden service layer

## Design Rules
- Prefer explicit modules over framework-style service layers.
- Extract a helper only when it removes real duplication, not just to make a file shorter.
- Accept a moderately large file when it still represents one coherent responsibility.
- Keep transport/protocol concerns at the edge.
- Keep the runtime core reusable without the browser UI or WebSocket server.
- Avoid introducing abstraction layers that exist only for future possibilities.

## Consequences
Positive:
- The runtime stays understandable without tracing through many layers.
- Core execution can evolve independently from UI and connector concerns.
- Features can still be added incrementally through hooks, helpers, and focused modules.

Trade-offs:
- Some files remain larger than they would in a framework-heavy architecture.
- Responsibility boundaries must be maintained with discipline instead of relying on a rigid folder hierarchy.

## Alternatives Considered
1. Split the runtime into many service objects
   - Rejected because it would add indirection without reducing current complexity.

2. Keep everything in a few large files
   - Rejected because the protocol edge and orchestration layers would keep pulling concerns into the core.

3. Adopt a formal layered architecture with handlers, services, repositories, and adapters everywhere
   - Rejected because it would overfit the project size and conflict with the goal of a lightweight runtime.

## Rollout Guidance
When making future changes:

1. First ask whether the change belongs to core runtime, context, memory, orchestration, or protocol edge.
2. If a boundary already exists, extend it instead of creating a new layer.
3. If a file is getting crowded, extract only the repeated or edge-specific pieces.
4. If a refactor makes the call graph harder to follow, it is probably too abstract.
