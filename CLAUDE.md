# CLAUDE.md

Guidance for Claude Code when working in this repository. Contains only what cannot be derived by reading the code — decisions, rationale, prohibitions, and roadmap.

---

## Core Contract

HushClaw is a composable LLM agent framework. Its non-negotiable invariants:

1. **The core runtime has zero mandatory dependencies** — `pip install hushclaw` on stock Python 3.11 must work. Optional features live behind `extras_require`.
2. **`server.py` is transport only** — HTTP/WebSocket I/O dispatch. Zero business logic. Every `_handle_*` method must eventually move out.
3. **Tools are framework-agnostic** — no tool module may import from `hushclaw.*` except `tools.base`. Dependencies come in via parameter injection.
4. **`event_stream()` is the primary API** — `run()` is a convenience wrapper. All callers should consume the typed event stream.
5. **Compaction is never disabled** — the fix for bad summaries is better compaction, not skipping it.

---

## Design Principles

### 1. Resilience First

Every failure mode has a pre-designed recovery path:

**Error taxonomy before recovery decisions.** Provider errors are classified into discrete categories before any handler decides what to do:
- `AUTH_FAILURE` → surface to user, stop
- `RATE_LIMIT` → exponential backoff, then credential rotation
- `CONTEXT_TOO_LONG` → compact + retry
- `TRANSIENT` → retry with backoff (max 3)
- `FATAL` → abort turn, emit `error` event

Implementation: `hushclaw/core/errors.py`. Classification is regex-based today (known debt — see Roadmap #4).

**Three-tier fallback chain** (aspirational — Tier 2/3 partially implemented):
1. **Credential pool rotation** — multiple API keys per provider, rotated on 429/402. Rotation strategies: `fill_first` (default), `round_robin`, `least_used`. Per-task leasing prevents concurrent rotation conflicts.
2. **Primary model fallback** — `cheap_model` for round 0; future: switch provider on persistent failure.
3. **Auxiliary task fallback** — compression, embedding, vision tasks should have independent provider chains so a failing embedding provider doesn't block the main loop.

**Graceful degradation rules:**
- Vector search fails → fall back to BM25 only (never surface to user)
- Embedding provider down → FTS-only recall
- Browser unavailable → browser tools silently absent from schema
- Trajectory write fails → log warning, continue turn (never interrupt)

**What violates this principle:**
- `except Exception: pass` or `except Exception: log.error(...)` without classification
- Surfacing `context_length_exceeded` as an error event (it is a signal to compact)
- Disabling trajectory recording to avoid errors instead of fixing fault tolerance

---

### 2. Safety by Design

Dangerous operations require explicit gates, not implicit trust.

**Seven-layer defense model** (current implementation depth noted):

| Layer | Mechanism | Status |
|-------|-----------|--------|
| 1. Tool authorization | `tools.enabled` coarse ACL; `_confirm_fn` injection for shell/writes | ✅ Implemented |
| 2. Dangerous command approval | `_confirm_fn=None` blocks by default; REPL injects interactive prompt | ✅ Implemented |
| 3. Shell pattern blocking | `_BLOCKED_PATTERNS` in `shell_tools.py` | ✅ Implemented |
| 4. Skill sandboxing | External skills treated as untrusted until reviewed | ⚠️ Partial — pip install runs in same venv |
| 5. Credential filtering | Error messages must not leak API keys or tokens | ❌ Not implemented |
| 6. Context file scanning | AGENTS.md / SOUL.md scanned for prompt injection | ❌ Not implemented |
| 7. Per-session tool ACLs | Narrow tool surface per session for multi-tenant | ❌ Planned |

**Confirmation modes for dangerous tools:**
- `_confirm_fn=None` → block (server callers default)
- `_confirm_fn=<interactive>` → REPL: prompt user once / for session / always / deny
- Never whitelist dangerous tools globally without explicit user config

**Multi-agent safety:**
- Subagents receive only explicit context — no parent conversation history leaks through
- Subagent depth limit: parent (depth 0) spawns children (depth 1); children cannot delegate further
- Inception prompting: org-context injected into every agent's system prompt to prevent role-flipping, instruction echoing, and infinite delegation loops
- Broadcast results are advisory — no agent can force another to execute code

**What violates this principle:**
- Tools that `import _memory_store` directly (bypasses injection gate)
- Storing session state in module-level globals (concurrency + isolation failure)
- Catching and ignoring errors from `_confirm_fn` callbacks

---

### 3. Token Economy

Every byte of context is a budget decision.

**System prompt architecture:**
- `stable_prefix` — role + AGENTS.md + SOUL.md. No date, no query-specific content. Provider KV-cache eligible. Never changes mid-session.
- `dynamic_suffix` — today's date + USER.md + score-gated memories. Rebuilt every query.
- The `(stable, dynamic)` tuple triggers Anthropic `cache_control: {type: ephemeral}` on the stable part, targeting ~75% input token cost reduction on multi-turn sessions.

**Explicit budgets (see `context/policy.py`). No section silently crowds out another:**
- `stable_budget` — cacheable prefix ceiling
- `dynamic_budget` — per-query content ceiling
- `history_budget` — turns kept in context window
- `memory_max_tokens` — injected memories hard cap

**Dual compression strategy** (Hermes pattern):
- **Gateway hygiene** (85% threshold) — prevents API failure when sessions accumulate between turns; character-based estimate acceptable here
- **Agent compaction** (configurable threshold) — primary compression inside the tool loop; uses API-reported token counts, not estimates
- Iterative refinement: re-compression updates the existing summary bullet list rather than generating a new one from scratch
- Lossless strategy: old turns are archived to `MemoryStore` (tagged `_compact_archive`) before replacement — nothing is lost, only moved

**Auxiliary task provider chains** (aspirational):
Compression, embedding, vision, and session-search should run on independent provider configurations to prevent one failing provider from blocking the main loop. Today these share the primary provider.

**Progressive memory loading:**
- Skill prompts load in three tiers: listing only → full SKILL.md → referenced files. Never load all skills into context.
- Memory injection: FTS-first shortcut (skip vector search if BM25 score ≥ 0.8). Score-gate, budget-cap, then session-cache (30s TTL).

**What violates this principle:**
- Assembling context (calling `context_engine.assemble()`) more than once per turn — it must be extracted to a shared helper
- Disabling compaction to avoid summarization artifacts
- Loading full skill content when a listing would suffice

---

### 4. Modularity via Composition

Prefer stateless function composition over inheritance chains.

**The four seams:**
1. `LLMProvider.complete() → LLMResponse` — single-method contract. Add a provider in one file.
2. `ContextEngine` — three-hook ABC (`assemble`, `compact`, `after_turn`). Swap whole engine without touching AgentLoop.
3. `@tool` decorator — only registration mechanism. No XML, no subclassing.
4. Tool context injection — parameter name matching (`_memory_store`, `_config`, `_gateway`, `_registry`, `_session_id`, `_loop`, `_confirm_fn`). Tools never import from the runtime.

**Parallel tool execution** (Hermes pattern, not yet implemented):
Tools should be categorized at registration time:
- `parallel_safe` — read-only, no state mutation (recall, fetch_url, get_time)
- `path_scoped` — file ops within declared boundaries (read_file, write_file)
- `serial` — interactive or state-mutating (run_shell, apply_patch, delegate_to_agent)

Parallel-safe tools in a single LLM response should execute concurrently. Serial tools always execute sequentially. This categorization belongs in `ToolDefinition`, not scattered across call sites.

**Server.py rule:** `server.py` dispatches to handlers. Handlers delegate to domain modules. If a `_handle_*` method is longer than ~30 lines, it belongs in a separate module. Planned extractions: `server/config_handler.py`, `server/skill_handler.py`, `server/provider_handler.py`.

**What violates this principle:**
- Adding business logic to `server.py` handler methods
- Tool files that `from hushclaw.memory.store import MemoryStore` directly
- ContextEngine subclasses that modify AgentLoop internals

---

### 5. Streaming and Observability

The system must be observable from outside without instrumenting internals.

**Event stream contract** — every meaningful state change yields a typed JSON event. Adding a new event type is always acceptable; removing or renaming one is a breaking change. Event types: `chunk`, `tool_call`, `tool_result`, `compaction`, `round_info`, `done`, `error`, `session`.

**Token accounting is first-class:** input + output tokens tracked per-turn, accumulated per-session, persisted to `turns` table. Cost estimation uses `cost_per_1k_*` config fields; 0.0 means "not configured, don't display."

**Trajectory recording** (`config.agent.trajectory_dir`) writes JSONL per session. Must be best-effort: trajectory failures must never interrupt a turn. Currently missing `try/except` at `loop.py:347–366` — this is a known bug.

**WebSocket reconnect safety:** sessions buffer events server-side and replay on reconnect. The UI must never lose state from a transient disconnect.

---

### 6. Zero Mandatory Dependencies

Core runtime runs on stock Python 3.11 with no pip installs.

**The rule:** nothing in `hushclaw/` except `providers/anthropic_raw.py`, `providers/openai_raw.py`, and test files may use a non-stdlib import at module level. All optional imports must be inside functions with `try/except ImportError`.

**Optional extras** (see `pyproject.toml [extras_require]`):
- `server` — websockets
- `sdk` — anthropic, openai SDKs
- `browser` — playwright
- `embeddings` — sentence-transformers or provider SDKs
- `all` — everything

---

### 7. User Modeling

The agent should build a persistent model of the user, separate from general memory.

**Two-file memory architecture** (Hermes pattern — partially implemented):
- `MEMORY.md` (~2k chars) — agent's notes: environment facts, project conventions, learned lessons. Loaded frozen at session start into `stable_prefix`.
- `USER.md` (~1.5k chars) — user profile: communication preferences, workflow habits, recurring goals. Injected into `dynamic_suffix` (refreshed per-query).

**USER.md is distinct from MEMORY.md:** MEMORY.md is about the world; USER.md is about the person. Mixing them degrades both.

**Capacity discipline:** when either file approaches its size limit, the agent must consolidate or remove stale entries before adding new ones. Overflow is an error, not a silent truncation.

**Cross-session search** complements bounded files: `search_notes` queries the full SQLite FTS index for context from distant sessions without consuming primary context budget.

---

## Security Model

Priority order when principles conflict: Safety > Modularity > Token Economy > Observability.

**Credential hygiene (not yet implemented — Roadmap #6):**
- Error messages must redact API keys, bearer tokens, and credential-shaped strings before surfacing to the user or writing to logs
- Pattern: `sk-[A-Za-z0-9]{20,}`, `Bearer [A-Za-z0-9+/=]{20,}`, generic `[A-Z_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*=\S+`
- MCP subprocess environments must strip credential-named env vars

**Context file injection scanning (not yet implemented — Roadmap #7):**
- AGENTS.md and SOUL.md loaded from workspace must be scanned before injection
- Red flags: hidden Unicode, base64 payloads, instructions that override the system prompt, credential exfiltration patterns

**Skill package trust (partially implemented):**
- Skills installed from external repos run in the same Python process as the server
- Before `registry.load_plugins()` is called for a new skill, the tool files should be scanned for suspicious imports (`subprocess`, `socket`, `os.system`, etc.)
- Current state: no scanning; `pip install` runs unaudited

**SSRF protection (not yet implemented):**
- `fetch_url` and browser tools must validate URLs against RFC 1918 private ranges, loopback, link-local, and cloud metadata hostnames (169.254.169.254)
- Redirect chains must be re-validated at each hop
- DNS failures should block (fail-closed), not silently succeed

---

## Architecture

```
CLI / WebSocket
  └─ Gateway                    # multi-agent routing, session affinity
       └─ AgentPool             # per-agent, per-session AgentLoop instances
            └─ AgentLoop        # ReAct event loop (loop.py)
                 ├─ ContextEngine.assemble()   # (stable_prefix, dynamic_suffix)
                 ├─ ContextEngine.compact()    # when history_budget exceeded
                 ├─ LLMProvider.complete()     # pluggable, single-method contract
                 ├─ ToolExecutor.execute()     # context injection, parallel dispatch
                 ├─ MemoryStore.save_turn()    # SQLite + Markdown persistence
                 └─ ContextEngine.after_turn() # regex fact extraction (zero LLM calls)
```

**Request flow — single agent:**
1. `assemble()` → `(stable_prefix, dynamic_suffix)` within budget
2. Per round: `needs_compaction()?` → `compact()` → emit `compaction` event
3. `provider.complete()` → emit `chunk` events
4. `executor.execute()` → emit `tool_call` / `tool_result` events
5. `memory.save_turn()` → persist tokens
6. `after_turn()` → regex fact extraction
7. Emit `done` with cumulative token counts

**Request flow — multi-agent:**
```
Gateway.execute()   → named agent pool (session-affinity routing)
Gateway.pipeline()  → sequential: output[N] becomes input[N+1]
delegate_to_agent() → fresh-context subagent (depth-limited to 1)
broadcast_to_agents() → parallel fan-out, results aggregated
```

**Server dispatch:**
```
HushClawServer
  ├─ process_request hook: WS Upgrade → pass through; HTTP GET → serve static
  ├─ _handle_client()  → per-connection lifecycle
  └─ _dispatch()       → routes WS message type to _handle_* method
                          each _handle_* should delegate to a domain module
```

**Config loading precedence** (later overrides earlier):
`defaults.py` → `~/Library/Application Support/hushclaw/hushclaw.toml` → `.hushclaw.toml` → env vars (`HUSHCLAW_*`, `ANTHROPIC_API_KEY`)

**Memory layers:**
1. `stable_prefix` — AGENTS.md + SOUL.md (frozen at session start, KV-cached)
2. `dynamic_suffix` — USER.md + score-gated recalled notes (rebuilt per query)
3. `MemoryStore.recall_with_budget()` — hybrid FTS (60%) + vector (40%), score-gated, budget-capped, session-cached
4. `search_notes` tool — full FTS index, on-demand, no budget limit

---

## Known Issues & Architecture Debt

These are confirmed violations of the design principles above. Fix them before adding new features in the affected areas.

### Critical

**[DEBT-1] server.py is a 3400-line monolith** (`server.py:1173–3309`)
- 23 `_handle_*` methods implement config management, skill installation, provider testing, update execution, workspace init, file upload
- Violates Core Contract #2 and Principle 4
- Fix: extract to `hushclaw/server/config_handler.py`, `skill_handler.py`, `provider_handler.py`. Each handler module exports a single `handle(msg, gateway) → dict` function.

**[DEBT-2] Context assembly called 3× per turn** (`loop.py:165, 201, 241`)
- `run()`, `stream_run()`, `event_stream()` each call `context_engine.assemble()` independently
- Wastes tokens on repeated memory recall; violates Principle 3
- Fix: extract `_build_context()` helper called once; cache result for the turn.

**[DEBT-3] Trajectory write not fault-tolerant** (`loop.py:347–366`)
- No `try/except` around `trajectory_writer.record()`; a disk error aborts the turn
- Violates Principle 1 (Resilience First) and Principle 5
- Fix: wrap in `try/except Exception: log.warning(...)`, continue.

### High

**[DEBT-4] Error classification is regex-based string matching** (`core/errors.py`)
- The design calls for typed error taxonomy; current implementation pattern-matches error message strings
- Fragile across provider updates; OpenAI vs Anthropic error schemas differ
- Fix: classify by HTTP status code + provider-specific error code field, not message text.

**[DEBT-5] Browser session never closed** (`loop.py:73–82`)
- `_browser_session` created per AgentLoop but no `__del__`, `cleanup()`, or context manager
- Playwright processes leak if sessions persist for hours
- Fix: implement `AgentLoop.aclose()` that calls `browser_session.close()`; call from server on session eviction.

**[DEBT-6] Broad `except Exception` in browser and executor** (`browser.py:223,229,301,325,408,425`; `tools/executor.py:64–66`)
- Violates Principle 1; errors are suppressed, not classified
- Fix: classify as TRANSIENT / FATAL / TIMEOUT before deciding whether to retry or surface.

### Medium

**[DEBT-7] Config values not range-validated** (`config/schema.py`)
- `compact_threshold`, `memory_min_score` accept any float; `compact_strategy` accepts any string
- Fix: add `__post_init__` validators; raise `ConfigError` with field name and valid range.

**[DEBT-8] Hybrid search weights not validated** (`memory/store.py:34–35`)
- `fts_weight + vec_weight` could sum > 1.0; scores inflate unpredictably
- Fix: assert `0.95 ≤ fts_weight + vec_weight ≤ 1.05` at `MemoryStore.__init__`.

**[DEBT-9] OSError silently swallowed in context assembly** (`context/engine.py:242, 256`)
- `except OSError: pass` hides permission errors vs genuinely missing files
- Fix: `log.warning("workspace file unreadable: %s — %s", path, e)` then continue.

**[DEBT-10] TOML writer not reusable** (`server.py:131–207`)
- `_dict_to_toml()` is a module-level function inside server.py
- Fix: move to `config/writer.py`; make it the canonical serializer used by both CLI and server.

### Not Yet Implemented (Planned)

**[TODO-1] Credential pool rotation** — Multiple API keys per provider with `fill_first` / `round_robin` / `least_used` strategies. Per-task leasing for concurrent rotation safety. Target: `providers/credential_pool.py`.

**[TODO-2] Parallel tool execution** — `ToolDefinition` needs a `parallel_safe: bool` flag. `ToolExecutor` should batch parallel-safe tools from a single LLM response into `asyncio.gather()`. Target: `tools/base.py`, `tools/executor.py`.

**[TODO-3] Credential redaction in error messages** — Before any error reaches the user or logs, strip credential-shaped strings. Target: `core/errors.py` post-processing step.

**[TODO-4] Context file injection scanning** — Scan AGENTS.md / SOUL.md for prompt injection before loading. Target: `context/scanner.py`.

**[TODO-5] SSRF protection in fetch_url** — RFC 1918 + loopback + metadata IP blocklist. Target: `tools/builtins/web_tools.py`.

**[TODO-6] Per-session tool ACLs** — `tools.enabled` is global today; sessions should be able to further restrict. Target: `gateway.py` session config + `tools/registry.py` filter.

**[TODO-7] Auxiliary task provider chains** — Compression, embedding, vision use the primary provider today. Each should have its own configurable provider entry. Target: `config/schema.py` + `context/engine.py`.

---

## Extension Points

### Adding a provider

1. Create `hushclaw/providers/my_provider.py` subclassing `LLMProvider` from `providers/base.py`.
2. Implement `complete(messages, model, system, tools, max_tokens, **kwargs) → LLMResponse`.
3. Map role `"tool"` messages to the API's tool-result format.
4. Optionally handle `system` as `str | tuple[str, str]` for cache-control.
5. Register in `providers/registry.py:get_provider()`.
6. Add to `PROVIDERS` array in `hushclaw/web/app.js`.

### Adding a built-in tool

1. Add `@tool`-decorated function to `tools/builtins/` (existing module or new file).
2. Use only `from hushclaw.tools.base import tool, ToolResult` — no other hushclaw imports.
3. Declare `parallel_safe=True` in the decorator if the tool is read-only (see TODO-2).
4. Add to `ToolsConfig.enabled` in `config/schema.py` if it should be on by default.
5. Import the module in `ToolRegistry.load_builtins()`.
6. For user-confirmable tools: accept `_confirm_fn=None`; call `_confirm_fn(description)` before mutating state; `None` means blocked.

### Adding a skill package

Skill packages are Git repos with a `SKILL.md` (system prompt) and optional `tools/*.py`. See `skill-packages/hushclaw-skill-pptx/` for reference. The `@tool` API is identical to built-in tools.

SKILL.md front-matter: `name`, `description`, `tags`, `author`, `version`, `has_tools`.

Tools in skill packages must follow the same injection contract as built-in tools. No direct imports from `hushclaw.*` except `tools.base`.

### Implementing a custom ContextEngine

```python
from hushclaw.context.engine import ContextEngine

class MyEngine(ContextEngine):
    async def assemble(self, query, policy, memory, config, session_id=None):
        return ("stable system prompt", f"dynamic: {query}")

    async def compact(self, messages, policy, provider, model, memory, session_id):
        return messages[-policy.compact_keep_turns:]

    async def after_turn(self, session_id, user_input, assistant_response, memory):
        pass

agent = Agent(context_engine=MyEngine())
```

---

## Commands

```bash
pip install -e .                    # core only (zero mandatory deps)
pip install -e ".[server]"          # + WebSocket server
pip install -e ".[all]"             # + all optional SDKs + browser

python -m pytest tests/ -v          # 128 tests
python -m pytest tests/test_X.py::ClassName::test_name -v  # single test

hushclaw serve                      # HTTP + WebSocket on port 8765
hushclaw serve --host 0.0.0.0      # bind all interfaces

make lint                           # py_compile syntax check only (no ruff/mypy)
make clean                          # remove __pycache__, build artifacts
```
