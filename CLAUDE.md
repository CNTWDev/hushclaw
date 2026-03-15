# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e .                    # install core (zero mandatory deps)
pip install -e ".[server]"          # install with WebSocket server support
pip install -e ".[all]"             # install with all optional SDKs + websockets

python -m pytest tests/ -v          # run all tests (67 total)
python -m pytest tests/test_gateway.py -v              # run a single test file
python -m pytest tests/test_gateway.py::TestGateway::test_broadcast_returns_dict -v  # single test

ghostclaw serve                     # start HTTP + WebSocket server on port 8765
ghostclaw serve --host 0.0.0.0     # bind to all interfaces (LAN/remote access)

make lint                           # syntax check via py_compile
make clean                          # remove __pycache__, build artifacts
```

No linter (ruff/flake8) or type-checker (mypy) is configured. `make lint` only runs `py_compile`.

## Architecture

The framework is a layered stack: **CLI → Agent → AgentLoop → ContextEngine + LLMProvider + ToolExecutor**, with a **Gateway** layer on top for multi-agent routing, and a **GhostClawServer** serving both HTTP (static Web UI) and WebSocket (protocol) on a single port.

### Request flow (single agent)

```
cli.py → Agent.new_loop() → AgentLoop.event_stream()   ← REPL uses event_stream, not run()
  → context_engine.assemble()  # build (stable_prefix, dynamic_suffix) within token budget
  → event loop (per round):
    → needs_compaction()?      # check history vs policy.history_budget
      → context_engine.compact()   # lossless: archive old turns to memory, replace with summary
      → yield {"type": "compaction", "archived": N, "kept": M}
    → provider.complete()      # LLMProvider.complete() returns LLMResponse
    → yield {"type": "chunk", "text": "..."}
    → ToolExecutor.execute()   # dispatches tool calls, injects _context vars
    → yield {"type": "tool_call", ...} / {"type": "tool_result", ...}
  → memory.save_turn()         # persist turn with input_tokens/output_tokens
  → context_engine.after_turn()  # regex-based fact extraction (DefaultContextEngine)
  → yield {"type": "done", "input_tokens": N, "output_tokens": M}
```

### Multi-agent request flow

```
CLI / WebSocket → Gateway
  → Gateway.execute()          # route to named AgentPool
  → Gateway.pipeline()         # sequential: each output → next input
  → AgentPool.execute()        # session-affinity: same session_id → same AgentLoop
```

### Web UI + server flow

```
Browser
  ├─ GET /          → GhostClawServer._http_handler() → ghostclaw/web/index.html
  ├─ GET /app.js    → ghostclaw/web/app.js
  ├─ GET /style.css → ghostclaw/web/style.css
  └─ WS  /          → GhostClawServer._handle_client() → _dispatch()

On WS connect:
  → server pushes {"type": "config_status", "configured": bool, ...}
  → if not configured → browser shows 4-step setup wizard
  → wizard sends {"type": "save_config", "config": {...}}
  → server writes TOML, responds {"type": "config_saved", "restart_required": true}
```

HTTP vs WebSocket is distinguished in `_http_handler()` via the `process_request` hook of `websockets.serve()`: requests with `Upgrade: websocket` header are passed through; ordinary HTTP GETs are served from `ghostclaw/web/`.

### Key design patterns

**ContextEngine** — Pluggable context lifecycle (`ghostclaw/context/`). The `assemble()` hook returns `(stable_prefix, dynamic_suffix)`:
- `stable_prefix` — static role prompt + `agent.instructions`; provider KV-cache eligible (no date)
- `dynamic_suffix` — today's date + score-gated memories (per-query, always fresh)

`compact()` implements lossless compaction: old turns are archived to `MemoryStore` (tagged `_compact_archive`) before being replaced by a summary bullet list. `after_turn()` in `DefaultContextEngine` runs lightweight regex-based fact extraction (zero LLM calls); disable with `context.auto_extract = false`.

**Tool context injection** — Tools receive shared objects via parameter name matching, not import. The `ToolExecutor` inspects the function signature and injects any matching keys from its context dict:
- `_memory_store` → `MemoryStore`
- `_config` → `Config`
- `_gateway` → `Gateway` (only after `agent.enable_agent_tools()` is called)
- `_registry`, `_session_id`, `_loop`
- `_confirm_fn` → callable (REPL sets this for `run_shell` to prompt before executing)

Parameters starting with `_` are excluded from the LLM-visible JSON schema (handled in `tools/base.py:_build_schema`).

**Tool registration** — The `@tool` decorator attaches a `ToolDefinition` to `fn._ghostclaw_tool`. `ToolRegistry.load_builtins()` registers all built-ins then filters to `tools.enabled`. Plugins auto-discovered from `plugin_dir/*.py`.

**Provider abstraction** — All providers implement `LLMProvider.complete() → LLMResponse`. The `anthropic-raw` provider uses only `urllib` (zero deps). Role `"tool"` in messages maps to Anthropic's `tool_result` content block format. `complete()` accepts `system` as either a plain `str` or a `(stable, dynamic)` tuple — the tuple triggers Anthropic content-block format with `cache_control: {type: ephemeral}` on the stable part.

**Config loading** — `loader.py` merges: defaults → `~/Library/Application Support/ghostclaw/ghostclaw.toml` → `.ghostclaw.toml` (project dir) → env vars. `ANTHROPIC_API_KEY` maps to `provider.api_key`. CLI flags are applied by setting `GHOSTCLAW_*` env vars before `load_config()`.

**Memory** — `MemoryStore` writes to both SQLite (`memory.db`, FTS5 + vectors) and Markdown files (`notes/YYYY-MM-DD/{id}-{slug}.md`). Hybrid search = 60% BM25 + 40% cosine. `recall_with_budget()` is the context-injection path: FTS-first (skips vector search if FTS score ≥ 0.8), score-gated (drops results below `memory_min_score`), budget-capped (stops at `memory_max_tokens`), and session-cached (30 s TTL per query). Token counts are persisted per turn in the `turns` table.

### Config sections

```toml
[agent]
model = "claude-sonnet-4-6"
max_tokens = 4096
max_tool_rounds = 10
system_prompt = "..."   # base role (no {date})
instructions  = "..."   # static rules → stable prefix, KV-cacheable

[provider]
name = "anthropic-raw"
# Token pricing for cost estimation (0.0 = not configured, no cost display)
cost_per_1k_input_tokens  = 0.0   # USD per 1k input tokens
cost_per_1k_output_tokens = 0.0   # USD per 1k output tokens

[context]
stable_budget      = 1500    # tokens for cacheable prefix
dynamic_budget     = 2500    # tokens for per-query content
history_budget     = 60000   # tokens of turns kept in context
compact_threshold  = 0.85    # compact when history exceeds this fraction
compact_keep_turns = 6       # always keep N most recent turns
compact_strategy   = "lossless"   # "lossless" | "summarize"
memory_min_score   = 0.25    # skip memories below this relevance score
memory_max_tokens  = 800     # hard cap on injected memories
auto_extract       = true    # regex-based fact extraction in after_turn (no LLM calls)

[server]
host = "127.0.0.1"           # 0.0.0.0 for LAN/remote access
port = 8765
api_key = ""                 # non-empty = require X-API-Key header

[gateway]
shared_memory = true
max_concurrent_per_agent = 10

[gateway.pipelines]
research_write = ["researcher", "writer"]   # named pipeline

[[gateway.agents]]
name = "researcher"
description = "Specialist for research tasks"
model = "claude-sonnet-4-6"   # empty = inherit global
tools = ["recall", "fetch_url"]            # empty = inherit global
```

### WebSocket protocol (full)

Server (`ghostclaw serve`) accepts:
- `{"type": "chat",           "text": "...", "agent": "default", "session_id": "..."}`
- `{"type": "pipeline",       "text": "...", "agents": ["a1","a2"]}`
- `{"type": "ping"}`
- `{"type": "get_config_status"}`
- `{"type": "save_config",    "config": {"provider": {...}, "agent": {...}}}`
- `{"type": "list_agents"}`
- `{"type": "list_sessions"}`
- `{"type": "list_memories",  "query": "", "limit": 20}`
- `{"type": "delete_memory",  "note_id": "..."}`

Server emits:
- `session`, `chunk`, `tool_call`, `tool_result`, `compaction`, `pipeline_step`, `done`, `error`, `pong`
- `config_status` — pushed automatically on every new WS connection
- `config_saved`
- `agents`, `sessions`, `memories`, `memory_deleted`

### Web UI files

| File | Role |
|------|------|
| `ghostclaw/web/index.html` | Page shell, tab nav, setup wizard modal (HTML skeleton) |
| `ghostclaw/web/app.js` | All JS: WS client, chat rendering, wizard 4-step flow, sessions/memories panels |
| `ghostclaw/web/style.css` | Dark theme CSS variables; wizard overlay/card/progress/field styles |

**Wizard steps (app.js):**
1. `renderStep1()` — provider radio cards (PROVIDERS array)
2. `renderStep2()` — API key (password) + base URL fields, adapted per provider
3. `renderStep3()` — model text input with `<datalist>` + quick-pick chip buttons
4. `renderStep4()` — review table + config file path + restart note
5. `renderWizardSuccess()` — replaces body on `config_saved` response

**Config save flow (server.py):**
- `_config_status()` — reads full config from `self._gateway._base_agent.config`, returns sanitized dict
- `_handle_save_config()` — reads existing user TOML via `_load_toml()`, merges wizard fields (skips empty strings), writes via `_dict_to_toml()`
- `_dict_to_toml()` — module-level minimal TOML serializer (scalars + simple lists + flat sections; no arrays-of-tables)

### Install scripts

| File | Platform |
|------|---------|
| `install.sh` | macOS + Linux |
| `install.ps1` | Windows PowerShell 5.1+ |

Both scripts:
1. Check Python 3.11+ (give platform-appropriate install guidance if missing)
2. Clone/update repo to `~/.ghostclaw/repo` (HTTPS, no SSH key required)
3. Create `~/.ghostclaw/venv` and install `ghostclaw[server]`
4. Detect local LAN IP + fetch public IP from `api.ipify.org`
5. Print three access URLs (loopback / LAN / internet)
6. Open browser automatically, then start `ghostclaw serve --host 0.0.0.0`

Flags: `--update` / `-Update`, `--start-only` / `-StartOnly`
Env overrides: `GHOSTCLAW_HOME`, `GHOSTCLAW_PORT`, `GHOSTCLAW_HOST`, `GHOSTCLAW_NO_BROWSER`

### Adding a new provider

1. Create `ghostclaw/providers/my_provider.py` implementing `LLMProvider` (subclass `providers/base.py`).
2. Register in `providers/registry.py:get_provider()`.
3. Map role `"tool"` messages to whatever the API expects.
4. Optionally handle `system` as `str | tuple[str, str]` for cache-control support.
5. Add to the `PROVIDERS` array in `ghostclaw/web/app.js` so the setup wizard lists it.

### Adding a built-in tool

1. Add `@tool`-decorated function to the appropriate module in `tools/builtins/` (or create a new one).
2. Import the module in `ToolRegistry.load_builtins()`.
3. Add the tool name to the default `tools.enabled` list in `config/schema.py:ToolsConfig` if it should be on by default.
4. For tools that need user confirmation in the REPL, accept a `_confirm_fn=None` parameter — the REPL injects an interactive prompt; other callers inject `None` (no confirmation).

### Bundled Skill Package format

External skill packages can bundle Python tools alongside the `SKILL.md` prompt. GhostClaw loads them automatically — no manual tool registration needed.

**Directory layout (standalone Git repo):**

```
your-skill-package/
  SKILL.md              ← LLM system prompt (required; see SKILL.md format below)
  tools/
    your_tools.py       ← @tool-decorated Python functions (optional)
  requirements.txt      ← pip dependencies for your tools (optional)
  README.md
```

**SKILL.md front-matter fields:**

```markdown
---
name: my-skill
description: One-line description shown in the Skill Store
tags: ["tag1", "tag2"]
author: Your Name
version: "1.0.0"
has_tools: true        ← declared in index.json for Store badge display
---
System prompt…
```

**Tool file (`tools/*.py`):**

```python
from ghostclaw.tools.base import ToolResult, tool

@tool(description="What this tool does.")
def my_tool(param: str) -> ToolResult:
    return ToolResult(output={"result": param})
```

- Use `from ghostclaw.tools.base import tool, ToolResult` — same API as built-in tools.
- All tools are synchronous unless you need async; the executor handles both.
- Parameters prefixed with `_` are injected by the framework (e.g. `_memory_store`, `_config`) and hidden from the LLM schema.

**Loading timeline:**

| Event | What happens |
|-------|-------------|
| `ghostclaw serve` startup | `agent.py` scans `skill_dir/*/tools/*.py` and calls `registry.load_plugins()` for each |
| Skills → Store → Install | `server.py` git clones repo → runs `pip install -r requirements.txt` → calls `registry.load_plugins(tools_dir)` |

**requirements.txt:** Standard pip requirements. GhostClaw installs into `sys.executable`'s environment (the same Python that runs `ghostclaw serve`), so tools can import the deps immediately after install — no restart needed.

**index.json `has_tools` field:** Skill Store entries can declare `"has_tools": true` in the index to display a 🔧 badge in the marketplace UI. The server passes this field through transparently.

**Reference implementation:** `skill-packages/ghostclaw-skill-pptx/` in this repo.

### Implementing a custom ContextEngine

```python
from ghostclaw.context.engine import ContextEngine
from ghostclaw.context.policy import ContextPolicy

class MyContextEngine(ContextEngine):
    async def assemble(self, query, policy, memory, config, session_id=None):
        # Return (stable_prefix, dynamic_suffix)
        return ("You are a custom agent.", f"Query: {query}")

    async def compact(self, messages, policy, provider, model, memory, session_id):
        # Compact and return smaller messages list
        return messages[-policy.compact_keep_turns:]

    async def after_turn(self, session_id, user_input, assistant_response, memory):
        pass  # or do custom post-turn work

agent = Agent(context_engine=MyContextEngine())
```

`DefaultContextEngine` accepts `auto_extract: bool = True` in its `__init__`. `AgentLoop` passes `config.context.auto_extract` automatically when constructing the default engine.
