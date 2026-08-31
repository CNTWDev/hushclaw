# Hermes Agent harness evolution and HushClaw v0.6 response

Research date: 2026-08-31
Upstream inspected: `NousResearch/hermes-agent` main at
`6681f9ebc30fe6c186a200f850cae627ed8bd7bc`

## Executive finding

The recent direction is not “put more intelligence in the central loop.” It is
the opposite: keep a narrow, cache-stable conversation kernel and move optional
capabilities into discoverable, cacheable edges. Latency work increasingly
treats cold start, schema/prompt churn, metadata refresh, and tool scheduling as
separate problems rather than one generic “agent is slow” problem.

## What changed in recent release lines

### Cold start became an architectural concern

The v0.12 line emphasized lazy imports, cached configuration reads, cached tool
definitions, and cached availability checks. The important shift is that tool
registration is no longer assumed to be free just because individual Python
functions are small. Import graphs, environment checks, and schema generation
are part of the user-perceived startup budget.

### Prompt caching became a conversation invariant

Current `prompt_caching.py` and `prompt_cache_boundary.py` keep stable material
ahead of explicit cache boundaries and avoid moving anchors between turns. Tool
definitions and effort settings are treated as parts of the cache identity.
The practical lesson: a per-turn “smart” tool subset can be slower overall if
it causes the provider to discard a large reusable prefix.

### Discovery replaced universal eager exposure

`tools/registry.py`, `tools/tool_search.py`, and the repository guidance describe
a capability ladder: extend an existing surface first, then CLI/skill,
service-gated capability, plugin, or MCP; add a core tool only last. The registry
can stay broad while the active model surface stays session-specific.

### Metadata adopted stale-while-revalidate

`agent/models_dev.py` uses memory → disk → network lookup, returns stale usable
data immediately, and refreshes it in the background with single-flight
coordination. `agent/moa_loop.py` similarly caches runtime/preset resolution.
This separates correctness-critical request work from freshness work.

### Tool execution became a scheduler problem

Recent executor/dispatch code groups operations that can run concurrently while
respecting ordering and path conflicts. The useful abstraction is not simply
“parallel=true”; it is a plan segmented by mutation and resource conflicts.

## Code paths inspected

- `agent/agent_init.py`, `agent/conversation_loop.py`: composition and loop edges
- `agent/prompt_caching.py`, `agent/prompt_cache_boundary.py`: stable prefix rules
- `agent/models_dev.py`: memory/disk/network SWR metadata cache
- `agent/moa_loop.py`: preset/runtime caches
- `agent/tool_executor.py`, `agent/tool_dispatch_helpers.py`: dispatch planning
- `tools/registry.py`, `tools/tool_search.py`: discovery and availability caching
- `hermes_cli/_startup_fast.py`: cold-path import discipline
- `tools/web_result_cache.py`, `tools/session_search_tool.py`: result reuse/search

Official upstream references:

- <https://github.com/NousResearch/hermes-agent>
- <https://github.com/NousResearch/hermes-agent/releases>
- <https://github.com/NousResearch/hermes-agent/blob/main/agent/models_dev.py>
- <https://github.com/NousResearch/hermes-agent/blob/main/agent/moa_loop.py>
- <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/configuration.md>

## HushClaw bottleneck diagnosis

The local cold baseline was already modest: importing `hushclaw.loop` was about
95–125 ms and CLI help about 3–6 ms on the test machine. The dominant perceived
delay is therefore normally inside a turn:

1. synchronous recall/workspace reads during async context assembly;
2. large tool schema payloads submitted on every provider round;
3. provider queue/network/model TTFT and generation;
4. network-bound search/fetch/skill operations and repeated tool rounds;
5. persistence and non-critical learning hooks after the answer.

The existing event envelope already measured most stages, but the data was hard
to aggregate. The v0.6 changes make the two locally controllable hot-path costs
smaller and turn the remaining stages into queryable evidence.

## Implemented in HushClaw v0.6

- exact, durable session-level tool-surface snapshots;
- eager common tools plus `tool_search`/`tool_call` long-tail discovery;
- underlying calls still pass through ToolRuntime policy/audit/verification;
- dynamic context construction moved off the asyncio event loop;
- `run_metrics` projection and v4 historical perf backfill;
- benchmark reports registry count, visible count, schema tokens, and reduction.

Measured default surface on this checkout: 72 registered, 21 visible, estimated
8,191 → 2,659 schema tokens, a 67.5% reduction. This is a payload reduction, not
a promise that end-to-end latency falls by exactly 67.5%; provider/network and
the number of reasoning/tool rounds remain independent contributors.

## Recommended next measurement cycle

Collect at least 30 real turns per provider and compare p50/p95 for:
`assemble_ms`, `ttft_ms`, `llm_ms`, `tool_ms`, and `total_ms`. Segment by model,
tool-surface fingerprint, and whether a turn used web/skill tools. Only after
that should the next optimization choose among provider connection reuse,
search-result SWR caches, speculative fetch, or conflict-aware tool scheduling.
