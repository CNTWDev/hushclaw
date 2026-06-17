# CLAUDE.md

Guidance for Claude Code. Contains only what cannot be derived by reading the code —
decisions, rationale, prohibitions, and open debt. Keep this file concise.

---

## Core Contract (non-negotiable)

1. **Zero mandatory dependencies** — `pip install hushclaw` on stock Python 3.11 must work. Optional features live behind `extras_require`. No non-stdlib import at module level outside `providers/anthropic_raw.py`, `providers/openai_raw.py`, and test files.
2. **`server_impl.py` is transport only** — HTTP/WebSocket dispatch. Zero business logic in `_handle_*` methods; each should delegate to a domain module.
3. **Tools are framework-agnostic** — no tool module may import from `hushclaw.*` except `tools.base`. All runtime dependencies arrive via parameter injection (`_memory_store`, `_config`, `_gateway`, `_session_id`, `_loop`, `_confirm_fn`, `_output_dir`, …).
4. **`event_stream()` is the primary API** — `run()` is a convenience wrapper.
5. **Compaction is never disabled** — fix bad summaries by improving compaction, not skipping it.

---

## Design Principles

### 1. Resilience First
Error taxonomy before recovery decisions (`hushclaw/core/errors.py`):
- `AUTH_FAILURE` → surface, stop | `RATE_LIMIT` → backoff + credential rotation
- `CONTEXT_TOO_LONG` → compact + retry | `TRANSIENT` → retry ×3 | `FATAL` → abort turn

Graceful degradation: vector search fails → BM25 only; browser unavailable → tools silently absent; trajectory write fails → log + continue. Never surface these to the user.

### 2. Safety by Design
- `_confirm_fn=None` blocks all dangerous tools by default (server path). REPL injects an interactive prompt.
- Subagent depth cap: parent (0) → child (1); children cannot re-delegate.
- SSRF protection in `fetch_url`: RFC 1918, loopback, link-local, cloud metadata IPs blocked before any socket opens (`tools/builtins/web_tools.py`).
- Skill sandboxing is **partial** — pip installs run unaudited in the same venv.

Priority when principles conflict: **Safety > Modularity > Token Economy > Observability**.

### 3. Token Economy
System prompt is a `(stable_prefix, dynamic_suffix)` tuple built from four internal tiers:
- `stable` — kernel identity/rules. Cacheable.
- `context` — workspace and operator instructions. Cacheable for the session.
- `volatile` — USER/profile/recall/session evidence. Rebuilt every query.
- `ephemeral` — turn-local hints such as timezone/language/runtime overlays. Never persisted.

Provider handoff remains two-part:
- `stable_prefix` = `stable + context`
- `dynamic_suffix` = `volatile + ephemeral`

Budgets are explicit (`context/policy.py`): `stable_budget`, `dynamic_budget`, `history_budget`, `memory_max_tokens`. No section silently crowds out another.

Memory recall: FTS shortcut (skip vector if BM25 ≥ 0.8) → score-gate → budget-cap → 30s session cache.

Skill loading is progressive: listing only → full SKILL.md → referenced files. Never load all skills at once.

Injected content is scanned before prompt reinjection:
- Workspace files (`AGENTS.md`, `SOUL.md`, `USER.md`)
- Recalled memory and session evidence
- Skill bodies
- Tool results

### 4. Modularity via Composition
Four extension seams:
1. `LLMProvider.complete() → LLMResponse` — single-method contract, one file per provider.
2. `ContextEngine` ABC (`assemble`, `compact`, `after_turn`) — swap without touching AgentLoop.
3. `@tool` decorator — only registration mechanism.
4. Parameter-name injection — tools declare what they need by naming parameters.

Capability ladder (highest leverage first, lowest kernel cost first):
1. Extend existing behavior.
2. Add or override a prompt block.
3. Add a skill.
4. Add a connector/plugin.
5. Add a deterministic retrieval/browsing path.
6. Add a new core tool only as a last resort.

**Parallel tool execution is implemented**: `ToolDefinition.parallel_safe=True` marks read-only tools. `event_stream()` in `loop.py` splits tool calls into dedup / parallel (`asyncio.gather`) / serial groups. Marked tools: `recall`, `search_notes`, `get_time`, `platform_info`, `read_file`, `list_dir`, `make_download_url`, `fetch_url`, `jina_read`.

### 5. Streaming and Observability
Event stream contract: `chunk`, `tool_call`, `tool_result`, `compaction`, `round_info`, `done`, `error`, `session`. Adding types is fine; removing/renaming is a breaking change.

Token accounting is first-class — persisted per turn and session to the `turns` table.

### 6. User Modeling & Learning
- `USER.md` — user profile (communication style, workflow, recurring goals). Injected into `dynamic_suffix`, distinct from `MEMORY.md` (world facts).
- Learning loop (`learning/controller.py`): captures tool traces per turn, runs `reflect_trace()`, persists to `reflections` + `skill_outcomes` tables. Quality score derived from trace: corrections → 0.0, errors → 0.6, clean → 1.0. Auto-patches single editable skills on strong signals.
- Memory creativity defaults enabled: `memory_decay_rate=0.002` (half-life ~350 days), `retrieval_temperature=0.1`.

---

## Web Access Stack

`fetch_url` priority: **curl_cffi** (Chrome TLS/h2 fingerprint, auto-installed) → **urllib fallback**.
Both paths: SSRF gate → request → CF challenge detection → decompress (gzip/deflate/brotli).
Proxy: reads `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` env vars; also accepts `proxies=` param.

Full web access priority for the LLM to use as a decision guide:
1. `fetch_url` — lightweight, TLS fingerprint, cookie jar, no JS rendering
2. `jina_read` — Jina proxy renders JS, returns clean markdown (200 req/day free)
3. `browser_navigate` + suite — full Playwright + playwright-stealth, handles Turnstile, login flows
4. `browser_connect_user_chrome` — CDP to user's existing Chrome (for already-logged-in sites)

---

## Skill Tiers

Four tiers, ascending priority (later overrides earlier):
| Tier | Directory | Who writes |
|------|-----------|------------|
| `builtin` | `skills/builtins/` (package) | Code; read-only |
| `system` | `_data_dir()/skills/` | Admin pre-install; shared |
| `user` | `_data_dir()/user-skills/` | WebUI install flow |
| `workspace` | `workspace/.hushclaw/skills/` | Per-project manual |

`user_skill_dir` is created on server startup (not lazily).

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
                 ├─ ToolExecutor.execute()     # injection + parallel/serial dispatch
                 ├─ MemoryStore.save_turn()    # SQLite + Markdown persistence
                 ├─ ContextEngine.after_turn() # regex fact extraction
                 └─ LearningController         # trace capture → reflection → skill patch
```

Config loading (later overrides earlier):
`defaults.py` → `~/Library/Application Support/hushclaw/hushclaw.toml` → `.hushclaw.toml` → env vars (`HUSHCLAW_*`, `ANTHROPIC_API_KEY`)

---

## Open Debt

Fix these before adding features in the affected area.

| ID | Severity | Description | Location |
|----|----------|-------------|----------|
| DEBT-7 | Low | Numeric budget range checks (`compact_threshold`, `memory_min_score`) not validated | `config/schema.py` — deferred |

### Still Planned (not yet implemented)

- **Credential pool rotation** — multi-key per provider, `fill_first`/`round_robin`/`least_used`. Target: `providers/credential_pool.py`.
- **Auxiliary task provider chains** — compression/embedding/vision each get independent provider config. Target: `config/schema.py` + `context/engine.py`.

---

## Extension Points

### Adding a provider
1. `hushclaw/providers/my_provider.py` subclassing `LLMProvider` from `providers/base.py`
2. Implement `complete(messages, model, system, tools, max_tokens, **kwargs) → LLMResponse`
3. Handle `system` as `str | tuple[str, str]` for cache-control
4. Register in `providers/registry.py:get_provider()`
5. Add to `PROVIDERS` in `hushclaw/web/app.js`

### Adding a built-in tool
1. `@tool`-decorated function in `tools/builtins/`
2. Only import `from hushclaw.tools.base import tool, ToolResult`
3. Mark `parallel_safe=True` if read-only
4. Add to `ToolsConfig.enabled` in `config/schema.py` for default-on
5. Import module in `ToolRegistry.load_builtins()`
6. Dangerous tools: accept `_confirm_fn=None`; call before mutating state

### Adding a skill package
Git repo with `SKILL.md` + optional `tools/*.py`. Same `@tool` injection contract as built-in tools. Front-matter: `name`, `description`, `tags`, `author`, `version`, `has_tools`.

### File output from tools
Tools that produce files: declare `_output_dir: Path | None = None`. Write to `_output_dir / filename`. Return `{"url": f"/files/{filename}", "path": str(path)}` in result JSON. The executor injects `config.server.upload_dir` automatically.

---

## Commands

```bash
pip install -e .                    # core only (zero mandatory deps)
pip install -e ".[server]"          # + WebSocket server
pip install -e ".[browser]"         # + Playwright + playwright-stealth
pip install -e ".[web]"             # + curl-cffi + brotlicffi
pip install -e ".[all]"             # everything

python -m pytest tests/ -v          # full test suite
python -m pytest tests/test_X.py::Class::test -v  # single test

hushclaw serve                      # HTTP + WebSocket on port 8765
hushclaw serve --host 0.0.0.0      # bind all interfaces

make lint                           # py_compile syntax check
make clean                          # remove __pycache__, build artifacts
```
