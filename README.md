# GhostClaw

Lightweight, token-first AI Agent framework with persistent memory and a built-in browser UI. Zero mandatory dependencies — pure Python stdlib.

## Features

- **Browser UI** — full chat interface served at `http://localhost:8765`; sessions, memories, and multi-agent management panels; setup wizard on first launch
- **Token-first design** — explicit token budget per context section; Anthropic KV-cache support for the stable prefix
- **Persistent memory** — notes survive across sessions via SQLite FTS5 + local vector search
- **Zero hard dependencies** — runs with Python 3.11+ stdlib only (`sqlite3`, `tomllib`, `asyncio`, `urllib`)
- **Multiple providers** — Anthropic (urllib or SDK), Ollama, OpenAI-compatible
- **ReAct loop** — tool use with pluggable `ContextEngine` for lossless compaction
- **Plugin tools** — drop `.py` files into `~/.config/ghostclaw/tools/` to extend
- **Multi-agent** — sequential pipelines, session-affinity pools, agent-to-agent delegation
- **Native storage paths** — macOS `~/Library/Application Support/ghostclaw/`, Linux `~/.local/share/ghostclaw/`

---

## Quick Install (one command)

### macOS / Linux

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/CNTWDev/ghostclaw/main/install.sh)
```

### Windows (PowerShell)

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
Invoke-WebRequest -Uri https://raw.githubusercontent.com/CNTWDev/ghostclaw/main/install.ps1 -OutFile install.ps1
.\install.ps1
```

The installer will:
1. Check for Python 3.11+ (installs guidance if missing)
2. Clone the repo to `~/.ghostclaw/`
3. Create a virtual environment and install all dependencies
4. Print **local, LAN, and public IP** access addresses
5. Open your browser automatically → the **setup wizard** appears on first launch

**Installer flags:**

```bash
bash install.sh --update      # pull latest code and restart
bash install.sh --start-only  # skip install, just start the server
```

**Environment overrides:**

| Variable | Default | Effect |
|---|---|---|
| `GHOSTCLAW_HOME` | `~/.ghostclaw` | Installation directory |
| `GHOSTCLAW_PORT` | `8765` | Server port |
| `GHOSTCLAW_HOST` | `0.0.0.0` | Bind address |
| `GHOSTCLAW_NO_BROWSER` | — | Set to `1` to skip browser auto-open |

---

## Manual Install (developer)

```bash
git clone https://github.com/CNTWDev/ghostclaw.git
cd ghostclaw

pip install -e ".[server]"          # core + WebSocket server
pip install -e ".[all]"             # core + all provider SDKs + server
pip install -e .                    # core only, no extra deps
```

---

## Web UI

Start the server and open your browser:

```bash
ghostclaw serve                     # binds to 127.0.0.1:8765
ghostclaw serve --host 0.0.0.0     # allow LAN/remote access
ghostclaw serve --host 0.0.0.0 --port 9000
```

```
┌─────────────────────────────────────────────────────────────┐
│  🐾 GhostClaw  [Agent: default ▾]  [Chat] [Sessions] [Memories]  ⚙ │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Chat panel  — streaming messages, collapsible tool bubbles │
│  Sessions panel  — browse & resume past sessions            │
│  Memories panel  — search, view, and delete memory notes    │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  [Message input________________________]  [Send]  [New]     │
│  session: s-abc12…  ● connected   In: 1,234  Out: 567       │
└─────────────────────────────────────────────────────────────┘
```

**UI features:**
- Real-time streaming — AI response chunks appear as they arrive
- Tool call bubbles — expand/collapse to see input and result
- Agent selector — switch between configured agents from a dropdown
- Sessions panel — click any past session to resume it in chat
- Memories panel — keyword search, per-note delete with confirmation
- Auto-reconnect — exponential backoff (1 s → 30 s) on disconnect
- ⚙ Settings button — reopen the setup wizard at any time

### Setup Wizard

On first launch (or when no API key is configured), the browser displays a **4-step setup wizard**:

| Step | Content |
|---|---|
| 1 | **Provider** — Anthropic / OpenAI-compatible / Ollama / Anthropic SDK |
| 2 | **API Key & Endpoint** — key field hidden, optional base URL override |
| 3 | **Model** — typed input with quick-pick chips and `<datalist>` suggestions |
| 4 | **Review & Save** — masked key, config file path, restart instruction |

The wizard writes only the changed fields into the user config TOML and instructs you to restart the server for changes to take effect.

---

## CLI

```
ghostclaw                              # Interactive REPL (readline history)
ghostclaw chat "message"               # Single query
ghostclaw chat --stream "message"      # Single query, streamed output
ghostclaw remember "fact to save"      # Write directly to memory
ghostclaw search "keyword"             # Search memory
ghostclaw sessions                     # List past sessions with token usage
ghostclaw sessions resume <id>         # Resume a session
ghostclaw tools list                   # Show available tools
ghostclaw config show                  # Dump current config as JSON
ghostclaw serve [--host H] [--port P]  # Start HTTP + WebSocket server
ghostclaw agents list                  # List configured agents
ghostclaw agents run <name> "task"     # Run through a named agent
ghostclaw agents pipeline "a,b" "task" # Pipeline: a's output → b's input
```

**REPL slash commands:**

| Command | Action |
|---|---|
| `/new` | Start a new session |
| `/remember <text>` | Save note to memory |
| `/search <query>` | Search memory |
| `/memories` | List user-saved memories (excludes auto-extracted noise) |
| `/memories --all` | List all memories including auto-extracted |
| `/memories --auto` | List only auto-extracted memories |
| `/forget <id>` | Delete a memory by ID prefix (with confirmation) |
| `/sessions` | List sessions with token usage |
| `/debug` | Show session/context/token/cost state |
| `/help` | Show help |
| `/exit` | Quit |

**REPL transparency:**

```
you> run the tests

  ⠸ thinking 1s
  [→ run_shell(command="python -m pytest tests/ -v")]
  Allow? [y/N] y
  [✓ 67 passed in 0.18s]
  ⠙ thinking 2s

ghostclaw> All 67 tests passed.

  ↳ In: 1,243 / Out: 18 tokens  ~$0.0008
```

Context compaction is announced inline:
```
  [Context compacted: 44 turns archived → 6 recent turns kept]
```

---

## Configuration

Config is loaded in priority order: **defaults → user config → project config → env vars**

| Platform | User config path |
|---|---|
| macOS | `~/Library/Application Support/ghostclaw/ghostclaw.toml` |
| Linux | `~/.config/ghostclaw/ghostclaw.toml` |
| Windows | `%APPDATA%\ghostclaw\ghostclaw.toml` |
| Project | `.ghostclaw.toml` in current directory (highest priority) |

The **setup wizard** writes directly to the user config file. You can also edit it manually:

```toml
[agent]
model = "claude-sonnet-4-6"
max_tokens = 4096
max_tool_rounds = 10
system_prompt = "You are GhostClaw, a helpful AI assistant with persistent memory."
instructions = ""   # static rules → stable cacheable prefix

[provider]
name = "anthropic-raw"       # anthropic-raw | anthropic-sdk | ollama | openai-raw
# Optional: set token prices to see cost estimates
cost_per_1k_input_tokens  = 0.003
cost_per_1k_output_tokens = 0.015

[memory]
embed_provider = "local"     # local | ollama | openai

[tools]
enabled = ["remember", "recall", "search_notes", "get_time", "platform_info"]
timeout = 30

[context]
stable_budget      = 1500    # tokens for role + instructions (KV-cached)
dynamic_budget     = 2500    # tokens for date + memories (per-query)
history_budget     = 60000   # max conversation tokens in context
compact_threshold  = 0.85    # compact when history exceeds this fraction
compact_keep_turns = 6       # always keep N most recent turns
compact_strategy   = "lossless"
memory_min_score   = 0.25
memory_max_tokens  = 800
auto_extract       = true    # regex-based fact extraction after each turn

[server]
host = "127.0.0.1"           # change to 0.0.0.0 for LAN/remote access
port = 8765
api_key = ""                 # non-empty = require X-API-Key header on WS connections
```

**Environment variables:**

| Variable | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key (when provider is openai-raw) |
| `GHOSTCLAW_PROVIDER` | Override provider name |
| `GHOSTCLAW_MODEL` | Override model name |
| `GHOSTCLAW_API_KEY` | Override provider API key |
| `GHOSTCLAW_DATA_DIR` | Override data directory |
| `GHOSTCLAW_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

---

## Providers

| Provider | Transport | Extra deps |
|---|---|---|
| `anthropic-raw` | `urllib` + JSON (default) | none |
| `anthropic-sdk` | Official `anthropic` library | `pip install ghostclaw[anthropic]` |
| `ollama` | Local HTTP `localhost:11434` | none (needs Ollama running) |
| `openai-raw` | `urllib` + JSON | none |

---

## Context Engine

### Two-section system prompt

```
┌─────────────────────────────────────────────────────────┐
│ STABLE PREFIX  (Anthropic KV-cache eligible)            │
│  - Base role prompt (no date)                           │
│  - agent.instructions (static rules / persona)          │
│  Budget: context.stable_budget (default 1500 tokens)    │
├─────────────────────────────────────────────────────────┤
│ DYNAMIC SUFFIX  (per-query, always fresh)               │
│  - Today's date                                         │
│  - Relevant memories (score-gated, budget-capped)       │
│  Budget: context.dynamic_budget (default 2500 tokens)   │
└─────────────────────────────────────────────────────────┘
```

### Lossless compaction

When history exceeds `compact_threshold × history_budget` the engine:
1. Archives all but the most recent `compact_keep_turns` turns to `MemoryStore` (tagged `_compact_archive`)
2. Replaces them with a bullet-point summary produced by one LLM call
3. Emits `{"type": "compaction", "archived": N, "kept": M}` (visible in REPL and Web UI)

### Automatic memory extraction

`after_turn()` runs lightweight regex patterns (URLs, file paths, versions, key=value pairs) and saves up to 3 facts per turn tagged `_auto_extract`. Zero LLM calls. Disable with `context.auto_extract = false`.

### Custom ContextEngine

```python
from ghostclaw.context.engine import ContextEngine
from ghostclaw.agent import Agent

class MyEngine(ContextEngine):
    async def assemble(self, query, policy, memory, config, session_id=None):
        return "You are my custom agent.", f"Today: {__import__('datetime').date.today()}"

    async def compact(self, messages, policy, provider, model, memory, session_id):
        return messages[-policy.compact_keep_turns:]

    async def after_turn(self, session_id, user_input, assistant_response, memory):
        pass

agent = Agent(context_engine=MyEngine())
```

---

## Memory System

Notes are stored simultaneously in:
- **Markdown** — `{data_dir}/notes/YYYY-MM-DD/{id}-{slug}.md`
- **SQLite** — `{data_dir}/memory.db` (FTS5 + vector embeddings + session turns table)

Search is hybrid and lazy:
```
1. FTS (BM25) search
2. If max FTS score ≥ 0.8: skip vector search
3. Otherwise: hybrid = 0.6 × BM25 + 0.4 × cosine
4. Filter: score ≥ memory_min_score (default 0.25)
5. Budget cap: stop at memory_max_tokens (default 800 tokens)
6. Session cache: same query within same session cached 30 s
```

| Internal tag | Meaning |
|---|---|
| `_compact_archive` | Turns archived during lossless compaction |
| `_auto_extract` | Facts extracted automatically by `after_turn()` |

---

## Built-in Tools

| Tool | Default | Description |
|---|---|---|
| `remember` | ✓ | Save information to persistent memory |
| `recall` | ✓ | Search memory and return relevant notes |
| `search_notes` | ✓ | Search notes, return titles and snippets |
| `get_time` | ✓ | Current date and time |
| `platform_info` | ✓ | OS and Python version |
| `read_file` | — | Read a file |
| `write_file` | — | Write a file |
| `list_dir` | — | List directory contents |
| `fetch_url` | — | HTTP GET a URL |
| `run_shell` | — | Execute a shell command (**explicit opt-in required**) |

`run_shell` is **not in the default `tools.enabled` list**. Enable deliberately:

```toml
[tools]
enabled = ["remember", "recall", "search_notes", "get_time", "platform_info", "run_shell"]
```

In the interactive REPL, `run_shell` always prompts for confirmation before executing. A built-in deny-list blocks the most destructive patterns (`rm -rf /`, `mkfs`, `dd if=`, fork bombs, etc.).

---

## Plugin Tools

Drop any `.py` file into `~/.config/ghostclaw/tools/` (macOS/Linux). Functions decorated with `@tool` are auto-discovered at startup:

```python
from ghostclaw.tools.base import tool, ToolResult

@tool(name="my_tool", description="Does something useful")
def my_tool(query: str, limit: int = 5) -> ToolResult:
    return ToolResult.ok(f"Result for: {query}")
```

---

## Multi-agent

```toml
[gateway]
shared_memory = true

[gateway.pipelines]
research_write = ["researcher", "writer"]

[[gateway.agents]]
name = "researcher"
description = "Specialist for research tasks"
tools = ["recall", "fetch_url"]
```

```bash
ghostclaw agents pipeline "researcher,writer" "Write a report on quantum computing"
ghostclaw agents run researcher "What is quantum entanglement?"
```

---

## WebSocket Protocol

The server (`ghostclaw serve`) speaks JSON over WebSocket on the same port as the HTTP UI.

**Client → Server:**

```json
{"type": "chat",         "text": "...", "agent": "default", "session_id": "s-xxx"}
{"type": "pipeline",     "text": "...", "agents": ["a1","a2"]}
{"type": "ping"}
{"type": "get_config_status"}
{"type": "save_config",  "config": {"provider": {"name": "anthropic-raw", "api_key": "..."}, "agent": {"model": "claude-sonnet-4-6"}}}
{"type": "list_agents"}
{"type": "list_sessions"}
{"type": "list_memories", "query": "optional keyword", "limit": 20}
{"type": "delete_memory", "note_id": "abc12345"}
```

**Server → Client (streaming):**

```json
{"type": "config_status",  "configured": true, "provider": "anthropic-raw", "model": "claude-sonnet-4-6", "api_key_set": true, "config_file": "..."}
{"type": "config_saved",   "ok": true, "config_file": "...", "restart_required": true}
{"type": "session",        "session_id": "s-xxx"}
{"type": "chunk",          "text": "Hello"}
{"type": "tool_call",      "tool": "recall", "input": {...}}
{"type": "tool_result",    "tool": "recall", "result": "..."}
{"type": "compaction",     "archived": 44, "kept": 6}
{"type": "pipeline_step",  "agent": "writer", "output": "..."}
{"type": "done",           "text": "...", "input_tokens": 120, "output_tokens": 45}
{"type": "agents",         "items": [{"name": "default", "description": ""}]}
{"type": "sessions",       "items": [...]}
{"type": "memories",       "items": [...]}
{"type": "memory_deleted", "note_id": "abc12345", "ok": true}
{"type": "error",          "message": "..."}
{"type": "pong"}
```

`config_status` is pushed automatically to every new WebSocket connection, so the UI can immediately display the setup wizard if the configuration is incomplete.

---

## Python API

```python
import asyncio
from ghostclaw.agent import Agent

agent = Agent()

# Single message
response = asyncio.run(agent.chat("What do you know about my projects?"))

# Streaming
async def stream():
    loop = agent.new_loop()
    async for chunk in loop.stream_run("Tell me a story"):
        print(chunk, end="", flush=True)
asyncio.run(stream())

# Persistent session
loop = agent.new_loop()
asyncio.run(loop.run("My name is Tuan"))
asyncio.run(loop.run("What is my name?"))

# Event stream (same as REPL and Web UI)
async def events():
    loop = agent.new_loop()
    async for event in loop.event_stream("Run the tests"):
        if event["type"] == "tool_call":
            print(f"Tool: {event['tool']}")
        elif event["type"] == "done":
            print(f"Tokens: {event['input_tokens']} in / {event['output_tokens']} out")
asyncio.run(events())

# Memory operations
agent.remember("GhostClaw uses Python 3.11+", title="Tech stack")
results = agent.search("Python")
notes   = agent.list_memories(limit=20)
agent.forget(notes[0]["note_id"])

agent.close()
```

---

## Project Structure

```
ghostclaw/
├── install.sh                  # macOS/Linux one-command installer
├── install.ps1                 # Windows PowerShell installer
├── pyproject.toml
├── Makefile
├── config/
│   └── ghostclaw.toml.example
├── ghostclaw/
│   ├── cli.py                  # Entry point: REPL + subcommands
│   ├── agent.py                # High-level Agent class
│   ├── loop.py                 # AgentLoop: ReAct + ContextEngine + event_stream
│   ├── gateway.py              # Multi-agent routing and session affinity
│   ├── server.py               # HTTP + WebSocket server (same port)
│   ├── exceptions.py
│   ├── web/                    # Browser UI (zero build step)
│   │   ├── index.html          # Single-page shell + setup wizard modal
│   │   ├── app.js              # WebSocket client, chat renderer, wizard logic
│   │   └── style.css           # Dark theme, no framework
│   ├── context/                # ContextEngine ABC + DefaultContextEngine + ContextPolicy
│   ├── config/                 # tomllib loader, dataclass schema
│   ├── memory/                 # SQLite FTS5 + vectors + Markdown
│   ├── providers/              # LLMProvider ABC + implementations
│   ├── tools/                  # @tool decorator, registry, executor
│   │   └── builtins/           # memory, system, file, web, shell, agent tools
│   └── util/                   # ids, token estimation, logging
└── tests/
```

---

## Development

```bash
# Run tests (67 total)
python -m pytest tests/ -v

# Install with server support
pip install -e ".[server]"

# Install with all optional deps
pip install -e ".[all]"

# Syntax check
make lint

# Clean build artifacts
make clean
```

---

## Requirements

- Python 3.11+ (uses `tomllib` from stdlib)
- No mandatory third-party packages
- An API key for your chosen provider (or a running Ollama instance)
- `websockets>=12.0` for `ghostclaw serve` (installed automatically by the install scripts)
