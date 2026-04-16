# Changelog

All notable changes to HushClaw are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

- No unreleased entries yet.

## [0.2.1] — 2026-04-16

### Performance

- After-turn enrichment (context learning, post_turn_persist hooks, trajectory recording) now runs in a background task via `asyncio.create_task`, removing it from the critical path of `event_stream`.
- `DefaultContextEngine` now caches workspace file reads (AGENTS.md, SOUL.md, USER.md) by mtime, avoiding redundant filesystem reads on every turn.
- User profile snapshot is TTL-cached (30 s) to avoid a SQLite round-trip each turn.
- Working-state file is mtime-gated per session to skip unnecessary reads.

### Web UI

- Replaced inline SVG logo in the startup modal with a shared `icon.svg` reference, reducing HTML/JS payload.
- CSS cleanup across `style.css`, `modal.css`, and `startup.css`.

### Fixes

- Web tools HTTPS handler and `jina_read` now use a custom SSL context via `hushclaw.util.ssl_context`.

## [0.2.0] — 2026-04-15

### Positioning

HushClaw is now described more explicitly as a long-lived agent runtime rather than a single-session chat wrapper.

- README positioning refreshed around persistent runtime, searchable sessions, compaction lineage, and preserved working state.
- Added ADR-0003 documenting the new runtime spine: lifecycle hooks, session intelligence, and compaction durability.
- Added ADR-0004 documenting the boundary between the small core runtime and protocol / orchestration edges.

### Runtime

- Added a first-class `HookBus` and wired it through the main `AgentLoop` lifecycle.
- Hook coverage now includes session init/restore, LLM calls, tool calls, compaction, and turn persistence.
- Shared turn preparation and finalization paths now keep the core loop smaller and more consistent across `run()`, `stream_run()`, and `event_stream()`.

### Memory Architecture

- Added explicit memory layering with `memory_kind`: `user_model`, `project_knowledge`, `decision`, `session_memory`, and `telemetry`.
- Added a dedicated `hushclaw.memory.kinds` policy module so storage, recall, and UI filtering use one shared definition.
- `remember()` now infers `memory_kind` from `note_type` by default while still allowing an explicit override.
- Auto-extracted memory now prefers user interests, preferences, beliefs, decisions, and stable project facts.
- Request-like task phrasing and action-like content are no longer auto-promoted into long-term semantic memory.
- Telemetry-style entries such as correction signals are now isolated from the main memory pool.

### Session Intelligence

- Added session metadata persistence via a dedicated `sessions` table.
- Added `session_lineage` records for compaction events.
- Added `turns_fts` for cross-session full-text search.
- Session records now track source, workspace, title, kind, compaction count, and last-compacted time.

### Context Durability

- Added pre-compaction working-state flush to `working_state.md`.
- Added post-compaction working-state reinjection so long-running tasks keep their thread after summarization.
- Working state is now structured into explicit sections:
  - `Goal`
  - `Progress`
  - `Open Loops`
  - `Recent Tool Outputs`

### Web UI

- Sessions sidebar now supports direct session search with result rendering in-place.
- Session cards expose more runtime metadata, including compaction count and source hints.
- Session history view now shows compaction summary and lineage before raw turn history, making compressed sessions inspectable.
- The Transsion knowledge/forum list now uses a cleaner single-line forum layout with clearer read/unread distinction.

### WebSocket / Server API

- `get_session_history` now returns `summary` and `lineage` in addition to raw turns.
- Added `search_sessions` for cross-session history search.
- Added `get_session_lineage` for lineage inspection.
- Memory listing now hides telemetry and session-internal memory kinds from the main user-facing memory panel.

### Versioning

- Version management now uses a single code-defined source via `hushclaw.__version__`.
- Packaging metadata and the CLI `--version` output now read from the same version value.

### Tests

- Added coverage for hook emission across `run()` and `event_stream()`.
- Added coverage for session search, session lineage persistence, working-state persistence, working-state injection, and working-state reinjection during compaction.
- Added coverage for `memory_kind` inference, recall filtering, and user-facing memory visibility rules.

## [0.1.4] — 2026-04-12

### Upgrade Flow: Race-Condition Fixes

Three bugs in the update/upgrade system have been closed:

**BUG #9 — TOCTOU race in `_handle_run_update`** (`server_impl.py`)
- Added `asyncio.Lock` (`_upgrade_lock`) covering both the active-session check and the
  `_upgrade_in_progress` flag set. A concurrent `run_update` message can no longer slip
  through the gap between check and start. A `finally` block unconditionally clears the
  flag even if the subprocess throws.

**BUG #5 — No graceful close before SIGTERM** (`server_impl.py`, `updates.js`, `websocket.js`)
- Server now broadcasts `{"type":"server_shutdown","reason":"upgrade"}` to all connected
  WebSocket clients immediately before handing off to the upgrade subprocess.
- New `handleServerShutdown` handler in `updates.js` arms `expectingDisconnect` and inserts
  a "Server is restarting for upgrade" status message, replacing the previous TCP-reset error
  that users saw when the process was killed without warning.

**BUG #11 — False upgrade success on reconnect** (`state.js`, `updates.js`, `websocket.js`)
- `requestRunUpdate` now captures `wizard.updateCurrentVersion` into
  `updateState.versionBeforeUpgrade` before sending the upgrade command.
- On reconnect, instead of immediately toasting "upgrade applied successfully", the UI shows
  "Reconnected — verifying upgrade…" and sets `verifyingUpgrade = true`.
- `handleUpdateStatus` detects the flag and compares old vs new version:
  - Version changed → "Upgraded 0.1.3 → 0.1.4" ✓
  - Version unchanged → warning "version still X — check server logs"
  - No prior version recorded → plain reconnect confirmation

---

## [0.1.3] — 2026-04-12

### Architecture: Hermes-Agent Upgrade

The core runtime has been refactored to match the [Hermes-Agent](https://github.com/NousResearch/hermes-agent) architecture pattern. This is the headline change of this release — a fundamental upgrade to how the agent reasons, remembers, and communicates.

**Prompt system overhaul (`hushclaw/prompts.py`)**
- All prompt text extracted into a single canonical module `hushclaw/prompts.py`, following Hermes's `prompt_builder.py` pattern. No more scattered inline strings across engine, config, and schema modules.
- `build_system_prompt(platform="")` factory supports per-channel formatting hints (Telegram, Feishu, Discord, Slack, CLI, cron) — the agent formats responses appropriately for the delivery channel.
- Compaction restructured: separate `COMPACT_SYSTEM` role ("context checkpoint — do NOT respond to questions"), structured `## Goal / Progress / Key Decisions / Pending User Asks / Critical Context` handoff template, and `COMPACT_SUMMARY_PREFIX` that prevents re-addressing already-completed work.
- `TOOL_USE_GUIDANCE` adds a positive execution mandate: "Keep working until the task is complete. Every response either makes progress via tool calls or delivers a final result."
- `MEMORY_GUIDANCE`, `SKILLS_GUIDANCE` added as first-class sections.

**Architecture debt cleared (10 items, `cba1a5a`)**
- DEBT-1: `server.py` 3,400-line monolith extracted into `server/config_handler.py`, `server/skill_handler.py`, `server/provider_handler.py`.
- DEBT-2: Context assembly called 3× per turn — consolidated to one call per turn.
- DEBT-3: Trajectory write not fault-tolerant — wrapped in `try/except`.
- DEBT-4: Error classification is regex string matching — classified by HTTP status + provider error code.
- DEBT-5 through DEBT-10: Browser session cleanup, broad `except` in executor, config range validation, hybrid search weight guard, OSError logging in context assembly, TOML writer extraction.

**Agent loop refactor (P0/P1/P2)**
- Skills-as-files: skill definitions live in `SKILL.md` files, not embedded memory blobs.
- Structured error recovery: errors classified into `AUTH_FAILURE / RATE_LIMIT / CONTEXT_TOO_LONG / TRANSIENT / FATAL` before retry decisions.
- Smart model routing: cheap model for round 0 when `cheap_model` is configured.
- Trajectory collection: JSONL per-session recording to `trajectory_dir`.

---

### Memory

- LLM distillation pass in Clean+Compact: summaries are generated by an LLM call, not just raw archival.
- `recall_count` boost + `max_age_days` gate: frequently-recalled notes score higher; notes older than threshold are excluded.
- Auto-extract hardened: drops markdown debris, stops on stop-phrases ("保存到记忆", "save to memory"), assistant channel restricted to hard artifacts (URL / path / version) only — eliminates low-quality junk notes.
- Proactive memory bootstrap: workspace `SOUL.md` / `USER.md` / `AGENTS.md` injected at correct cache tier (stable vs. dynamic prefix).
- FTS5 schema fix for proper full-text recall indexing.

---

### Connectors

- **Streaming removed** from Telegram and Feishu connectors. Connectors now collect the full LLM response and send it once — eliminates Telegram API rate-limit friction and the markdown rendering bug caused by partial HTML conversion of streaming chunks.
- **Telegram Markdown rendering**: `_md_to_tg_html()` converts LLM Markdown → Telegram HTML (`parse_mode=HTML`). On failure, `_strip_markdown()` sends clean plain text — no more raw `**syntax**` visible to users.
- Long replies split at paragraph boundaries with `(1/2)` indicators.
- Per-connector `markdown: bool` config flag.
- Hot-reload on config save; connector status dots in Channels panel.
- Multi-workspace routing: connectors bind to a named workspace via config.

---

### Providers

- **Google Gemini** provider (`google-genai>=1.0`): full tool-use support.
- **Transsion / TEX AI Router**: enterprise router managing multiple upstream LLMs behind a single credential, with OTP login flow.
- Dynamic provider labels in logs and error messages — no more hardcoded "Anthropic" references.

---

### WebUI

- **Three UI themes**: Indigo (deep navy + violet, default), Rose (deep-purple + rose-pink, Dracula-inspired), Ember (near-black charcoal + fire-red, derived from iTerm2 "Tomorrow Night Burns").
- **Version + build timestamp in header**: `HushClaw v0.1.3 · 2026-04-12 19:15`. `make stamp` updates `hushclaw/_build_info.py` before each commit.
- **Secrets skill**: API keys managed conversationally via `set_api_key` / `list_api_keys` tools, removed from setup wizard.
- Tab bar icons; Ladder-inspired markdown typography with tight code blocks.
- Startup overlay + reconnect banner for clear connection state.
- Share-to-Forum on AI messages; chat PDF export.
- Slash skill suggestions in chat input (`/` triggers autocomplete).
- Tool execution timeline track style replacing raw tool-call tips.
- Active tab persisted via URL hash.

---

### Skills

- Skill packages installable from Git repos via WebUI (one-click install).
- Delete button for non-builtin skills in WebUI.
- Slash skill routing: `/skill-name` directs a message to a specific skill agent.
- `remember_skill` proactively suggested after completing complex workflows.
- Skills discovered from `SKILL.md` files (skills-as-files, no memory dependency).
- New bundled skill packages: `stock-query`, `market-intelligence-commander`, `html-deck` (McKinsey-style HTML slide generator), `pptx-editor`, `x-operator`, data analytics suite (5 packages), product R&D suite (5 packages).

---

### Multi-Workspace

- Per-workspace session tagging and full UI data isolation.
- `AGENTS.md` in workspace directory overrides `config.agent.instructions` at runtime.
- Workspace selector in sidebar; workspace filter applied to all session queries.

---

## [0.1.2] — 2026

Initial stable release. See earlier changelog entries for cumulative history.

---

## [0.1.0] — 2026-03-28

### Update System

- **GitHub-based update checks** (`hushclaw/update/`): dedicated `provider` / `service` / `executor` module trio.
  - `GithubReleaseProvider` fetches from `/releases/latest`; falls back to `/tags` when no formal Releases exist; raises `NoReleasesError` (treated as "already up to date") when neither is present.
  - `UpdateService`: semantic-version comparison, per-channel cache (15 min TTL), asyncio mutex to prevent concurrent outbound requests.
  - `UpdateExecutor`: runs `install.sh --update` / `install.ps1 -Update` (managed installs) or `pip install -U hushclaw` (fallback); streams stdout progress back to the UI.
- **WebSocket protocol** — four new message types: `check_update`, `run_update`, `save_update_policy`, `update_status` / `update_available` / `update_progress` / `update_result`.
- **Auto-check on connect**: configurable interval (default 24 h); skipped if recently checked.
- **Upgrade confirmation dialog**: `update_available` prompts the user; blocks upgrade while active sessions are running (override with `force_when_busy`).
- `UpdateConfig` added to config schema; persisted in user TOML under `[update]`.

### Web UI

- **Memory tab in Settings modal**: all `[context]` config fields are now editable in the browser.
  - *Context & Compaction* — `history_budget`, `compact_threshold`, `compact_keep_turns`, `compact_strategy`
  - *Memory Retrieval* — `memory_min_score`, `memory_max_tokens`, `retrieval_temperature`, `serendipity_budget`
  - *Memory Decay* — `memory_decay_rate` (Ebbinghaus λ), `auto_extract` toggle
- **Shared modal component** (`modules/modal.js`): `openConfirm()` / `openDialog()` replace raw `window.confirm` for theme-consistent dialogs (used by update prompts and Service Worker reload).
- **Agent org-chart** (`modules/panels.js`):
  - Reporting lines drawn from subordinate (right column) to manager (left column) using `getBoundingClientRect()` for correct SVG-space coordinates — fixes persistent mis-positioning caused by `offsetLeft` being relative to each card's own column container.
  - Column gap increased to 52 px (×1.5) for clearer visual separation.
  - Link contrast: default `stroke: var(--text); opacity: 0.58; stroke-width: 2.9`; active highlight `stroke: var(--accent); stroke-width: 3.8`.
- **Light theme & system-aware appearance**: auto-switches between dark and light based on `prefers-color-scheme`; manual override via UI.
- **Drag-and-drop file attachments** in chat input.
- **Sessions sidebar**: collapsible left panel replacing the separate Sessions tab; shows name, token counts, timestamp.
- **Copy chat as Markdown / image**: two-button copy action on assistant messages; image copy with watermark.
- **`@mention` agent routing**: type `@agent_name` to direct a message to a specific agent; falls back to default.
- **Agent hierarchy & commander model**: commander / specialist role tags; `reports_to` field drives org-chart.

### Installer & CLI

- `install.sh` / `install.ps1`: robust Python 3.11+ detection on macOS regardless of PATH ordering; LAN + public IP printed at startup; `--update` / `--start-only` flags; env overrides `HUSHCLAW_HOME`, `HUSHCLAW_PORT`, `HUSHCLAW_HOST`, `HUSHCLAW_NO_BROWSER`.
- PATH configured automatically (`~/.local/bin` on macOS/Linux; user PATH on Windows).

### Architecture

- `refactor(architecture)`: unified session state; reduced coupling between server and agent loop.
- `feat(config)`: zero limits treated as unlimited in runtime validation and settings wizard.

---

## [0.0.3] — 2026-03-15

### Creativity Engine (Memory)

- **Memory time-decay** (`memory_decay_rate`): Ebbinghaus forgetting curve applied to recall scores — `score × e^(-λ × age_days)`. Older memories are gradually down-ranked. `0.03` ≈ 23-day half-life; `0.1` ≈ 7-day half-life. Default `0.0` = no decay (unchanged behaviour).
- **Retrieval temperature** (`retrieval_temperature`): Softmax-weighted random sampling over candidate memories. `0.0` = deterministic top-k (existing behaviour); `>0` = stochastic recall for creative associations.
- **Serendipity injection** (`serendipity_budget`): Fraction of `memory_max_tokens` filled from random notes (query-independent), appended as `## Serendipitous memories` in the dynamic suffix. Creates cross-domain associations at no extra LLM cost.
- **Abstractive compact strategy** (`compact_strategy = "abstractive"`): Compact prompt instructs the LLM to extract transferable PATTERNS and PRINCIPLES rather than verbatim facts. Result saved to memory tagged `_compact_abstractive`.
- `fts.py` / `vectors.py`: propagate `created` timestamp into search result dicts for decay calculation.
- `MemoryStore._random_sample_notes()`: SQLite `ORDER BY RANDOM()` path for empty-query serendipity.

### Web UI

- **Sessions merged into Chat as left sidebar**: session list now lives in a collapsible left panel alongside the chat; no separate tab needed.
- **Todos + Scheduled Tasks panel**: dedicated UI section showing pending todos and upcoming scheduled tasks.

### Browser Tools

- **Playwright browser tools** (`browser_navigate`, `browser_get_content`, `browser_click`, `browser_fill`, `browser_submit`, `browser_screenshot`, `browser_evaluate`, `browser_close`, `browser_open_for_user`, `browser_wait_for_user`): full headless browser control available to agents.
- **Privacy handover**: `browser_open_for_user` / `browser_wait_for_user` allow agents to hand off browser control to the human for login / CAPTCHA, then resume automation.
- **Cookie persistence**: storage state (cookies + localStorage) optionally saved across sessions (`browser.persist_cookies = true`).
- Auto-install Playwright on first use; browser tools enabled by default (`browser.enabled = true`).

### Installer & CLI

- **PATH setup**: `install.sh` / `install.ps1` now configure `~/.local/bin` on macOS/Linux and user PATH on Windows so `hushclaw` works immediately after install without reloading the shell manually.
- `--update` / `--start-only` flags; env overrides `HUSHCLAW_HOME`, `HUSHCLAW_PORT`, `HUSHCLAW_HOST`, `HUSHCLAW_NO_BROWSER`.

### UI & Connectors

- **Tabbed settings modal**: replaced 4-step wizard with a tabbed interface (🤖 Model / 📡 Channels / ⚙ System) — easier to adjust individual settings without re-running the full wizard.
- **@mention agent autocomplete**: type `@` in the chat input to autocomplete configured agent names for quick agent-switching.
- Provider redirect fix: `anthropic-raw` now follows `307`/`308` redirects on POST requests.

### Logging

- Default log level changed to `INFO`; flow-level events logged in `loop.py`, `gateway.py`, and `agent_tools.py` for production visibility.

---

## [0.0.2] — Architecture: Token-First ContextEngine + Multi-Agent Gateway

> Internal milestone. First version with the full production architecture.

### Token-First ContextEngine

- **Two-section system prompt**: `stable_prefix` (role + instructions, KV-cache eligible, no date) + `dynamic_suffix` (today's date + score-gated memories, per-query).
- **`ContextEngine` ABC** (`hushclaw/context/`): pluggable lifecycle with `assemble()`, `compact()`, `after_turn()` hooks.
- **`DefaultContextEngine`**: ships with lossless compaction (`compact_strategy = "lossless"` archives old turns to `MemoryStore` before replacing them with a bullet-point summary) and regex-based auto fact extraction in `after_turn()` (zero LLM calls).
- **`ContextPolicy` dataclass**: explicit token budgets — `stable_budget`, `dynamic_budget`, `history_budget`, `compact_threshold`, `compact_keep_turns`.
- **Context compaction** announced inline in REPL and emitted as `{"type": "compaction"}` event over WebSocket.

### Multi-Agent Gateway

- **`Gateway`** class: named `AgentPool` routing with session-affinity (same `session_id` always routes to the same `AgentLoop`).
- **Sequential pipelines**: each agent's output becomes the next agent's input; configurable via `[gateway.pipelines]` TOML.
- **Agent-to-agent delegation**: `enable_agent_tools()` injects `_gateway` context variable into tool executor, allowing tools to call other agents at runtime.
- **Session GC**: `gateway.session_ttl_hours` controls when idle `AgentLoop` instances are evicted.

### WebSocket Server + Browser UI

- **Single-port server** (`hushclaw serve`): HTTP static files + WebSocket protocol share one port via `process_request` hook of `websockets.serve()`.
- **Full chat UI** (`hushclaw/web/`): dark theme, zero framework, zero build step — `index.html` + `app.js` + `style.css`.
- **Auto-reconnect**: exponential backoff 1 s → 30 s.
- **4-step setup wizard** (original): triggered when no API key is configured; writes TOML via stdlib-only `_dict_to_toml()`.
- **Management panels**: Agents, Sessions, Memories — list, search, delete via WebSocket messages.

### Provider Abstraction

- `LLMProvider` ABC with `complete()` → `LLMResponse`; `stream()` → `AsyncIterator[str]`.
- `anthropic-raw`: `urllib` only, zero deps, supports Anthropic tool-use format and content-block system prompts with `cache_control: ephemeral` for KV caching.
- `anthropic-sdk`, `ollama`, `openai-raw` (OpenAI-compatible, also covers AIGOCODE proxy).
- Provider retry with exponential backoff (`max_retries = 3`, `retry_base_delay = 1.0`).

### Memory System

- **Hybrid search**: 60% BM25 (FTS5) + 40% cosine similarity; FTS-shortcut skips vector search when top FTS score ≥ 0.8.
- **Local TF-IDF vectors**: pure stdlib fallback — no external embedding provider required.
- **Dual storage**: every note written to both SQLite (`memory.db`) and `notes/YYYY-MM-DD/{id}-{slug}.md`.
- **`recall_with_budget()`**: score-gated (`memory_min_score`), budget-capped (`memory_max_tokens`), session-cached (30 s TTL).
- Token counts persisted per turn in the `turns` table; `sessions` subcommand shows per-session token usage.

### Tools

- **`@tool` decorator**: attaches `ToolDefinition` (name, description, JSON schema) to function; parameters starting with `_` are excluded from LLM-visible schema and injected by `ToolExecutor` from context (`_memory_store`, `_config`, `_gateway`, `_session_id`, `_loop`, `_confirm_fn`).
- **Per-tool timeout**: `@tool(timeout=N)` overrides global executor timeout.
- **`run_shell`**: explicit opt-in, REPL prompts for confirmation, built-in deny-list blocks destructive patterns.
- **Plugin auto-discovery**: `.py` files in `tools.plugin_dir` auto-loaded at startup.
- **Skill system**: `remember_skill` / `recall_skill` / `list_my_skills`; skills stored as Markdown in `skill_dir/`.

### Scheduled Tasks & Todos

- `scheduled_tasks` SQLite table + cron-style scheduler; `add_todo` / `list_todos` / `complete_todo` tools.
- REPL slash commands: `/new`, `/remember`, `/search`, `/memories`, `/forget`, `/sessions`, `/debug`, `/help`.

---

## [0.0.1] — Initial prototype

- Minimal `Agent` + `AgentLoop` wiring provider → tool executor → memory.
- `anthropic-raw` provider only.
- SQLite FTS5 memory, Markdown notes.
- Basic REPL.
