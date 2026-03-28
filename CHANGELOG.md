# Changelog

All notable changes to HushClaw are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

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
