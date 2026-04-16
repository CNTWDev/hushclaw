# ADR-0003: Runtime Hooks and Session Intelligence

## Status
Accepted

## Context
HushClaw started with strong primitives for memory, compaction, and persistent turns, but several long-running agent capabilities were still too implicit:

- lifecycle events were embedded directly in `loop.py`
- compaction preserved summaries but not an explicit working-state checkpoint
- persisted sessions could be listed, but not treated as first-class searchable, inspectable runtime objects
- the browser UI could resume history, but compressed-session context was not fully visible

To support a longer-lived agent product, the runtime needed a clearer spine for extension and a richer session model.

## Decision
Introduce two linked architectural layers:

1. Runtime hooks
   - Add a `HookBus` that emits structured lifecycle events from `AgentLoop`
   - Cover `pre_session_init`, `post_session_restore`, `pre/post llm`, `pre/post tool`, `pre/post compact`, and `post_turn_persist`
   - Treat WebSocket event streaming as one consumer, not the lifecycle system itself

2. Session intelligence
   - Add a `sessions` metadata table and `session_lineage` table
   - Add `turns_fts` for cross-session search
   - Persist session-level attributes such as source, workspace, kind, title, compaction count
   - Save a compact `working_state.md` before compaction and re-inject it afterward
   - Return `summary` and `lineage` alongside session history for UI rendering

## Consequences
Positive:
- New runtime features can attach to lifecycle hooks instead of patching core control flow
- Compaction becomes inspectable and less lossy for ongoing work
- Session history becomes a product feature, not only a storage detail
- Search, resume, and lineage views all share the same backend model

Trade-offs:
- More schema surface in SQLite
- Slightly more complexity in `AgentLoop` and session rendering
- Working-state extraction is currently heuristic and will need future refinement

## Alternatives Considered
1. Keep lifecycle logic inline in `loop.py`
   - Rejected because every new feature would further entangle core control flow.
2. Store only compaction summaries
   - Rejected because summaries alone do not preserve enough task continuity for long-running sessions.
3. Build session search purely in the UI from loaded history
   - Rejected because cross-session search needs indexed persistence and should not depend on full history downloads.

## Rollout Plan
1. Introduce `HookBus` and wire the loop lifecycle through it.
2. Add session metadata, lineage persistence, and turns FTS search.
3. Expose session search, lineage, and summary data over the WebSocket API.
4. Render search, summary, and lineage in the browser UI.
5. Evolve working-state extraction from heuristic text to richer structured state.
