# GhostClaw

Lightweight, token-first AI Agent framework with persistent memory. Zero mandatory dependencies — pure Python stdlib.

## Features

- **Token-first design** — explicit token budget per context section; Anthropic KV-cache support for stable prefix
- **Persistent memory** — notes survive across sessions via SQLite FTS5 + local vector search
- **Zero hard dependencies** — runs with Python 3.11+ stdlib only (`sqlite3`, `tomllib`, `asyncio`, `urllib`)
- **Multiple providers** — Anthropic (urllib or SDK), Ollama, OpenAI-compatible
- **ReAct loop** — tool use with pluggable `ContextEngine` for lossless compaction
- **Plugin tools** — drop `.py` files into `~/.config/ghostclaw/tools/` to extend
- **Native storage paths** — macOS `~/Library/Application Support/ghostclaw/`, Linux `~/.local/share/ghostclaw/`
- **Transparent REPL** — tool calls, token stats, and cost estimates shown in real time

## Install

```bash
pip install -e .                    # core, no extra deps
pip install -e ".[anthropic]"       # + official anthropic SDK (optional)
pip install -e ".[all]"             # + all provider SDKs
```

## Quick Start

```bash
export ANTHROPIC_API_KEY=sk-...

ghostclaw                           # interactive REPL
ghostclaw chat "What time is it?"   # single message
ghostclaw chat --stream "Tell me a story"  # stream output as it arrives
```

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
ghostclaw serve                        # Start the WebSocket server
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
| `/memories` | List user-saved memories (paginated, excludes auto-extracted noise) |
| `/memories --all` | List all memories including auto-extracted |
| `/memories --auto` | List only auto-extracted memories |
| `/forget <id>` | Delete a memory by ID prefix (with confirmation) |
| `/sessions` | List sessions with token usage |
| `/debug` | Show session/context/token/cost state |
| `/help` | Show help |
| `/exit` | Quit |

**REPL session continuity:**

When you start the REPL with no `--session` flag, GhostClaw offers to resume the most recent session:

```
Resume last session s-a1b2c3d4e5f6... (2026-03-11 14:23, 18 turns)? [Y/n]
```

Press Enter or `y` to continue; `n` to start fresh.

**REPL transparency:**

Each turn shows tool activity inline and a per-turn token summary:

```
you> run the tests

  ⠸ thinking 1s
  [→ run_shell(command="python -m pytest tests/ -v")]

  [run_shell] $ python -m pytest tests/ -v
  Allow? [y/N] y

  [✓ 67 passed in 0.18s
  (exit 0)]
  ⠙ thinking 2s

ghostclaw> All 67 tests passed.

  ↳ In: 1,243 / Out: 18 tokens  ~$0.0008
```

Context compaction is announced inline:
```
  [Context compacted: 44 turns archived → 6 recent turns kept]
```

On any API or network error, GhostClaw offers a retry prompt before discarding the turn.

## Configuration

Config is loaded in priority order: **defaults → user config → project config → env vars**

| Platform | User config path |
|---|---|
| macOS | `~/Library/Application Support/ghostclaw/ghostclaw.toml` |
| Linux | `~/.config/ghostclaw/ghostclaw.toml` |
| Project | `.ghostclaw.toml` in current directory (highest priority) |

Copy `config/ghostclaw.toml.example` to get started. Key settings:

```toml
[agent]
model = "claude-sonnet-4-6"
max_tokens = 4096
max_tool_rounds = 10
system_prompt = "You are GhostClaw, a helpful AI assistant with persistent memory."
instructions = ""   # static rules injected into the cacheable stable prefix

[provider]
name = "anthropic-raw"       # anthropic-raw | anthropic-sdk | ollama | openai-raw
# Optional: set token prices to see cost estimates in /debug and per-turn stats
# claude-sonnet-4-6 pricing as of 2025: $3/M input, $15/M output
cost_per_1k_input_tokens  = 0.003
cost_per_1k_output_tokens = 0.015

[memory]
embed_provider = "local"     # local | ollama | openai

[tools]
enabled = ["remember", "recall", "search_notes", "get_time", "platform_info"]
timeout = 30
# To enable shell execution (requires explicit opt-in):
# enabled = ["remember", "recall", "search_notes", "get_time", "platform_info", "run_shell"]

[context]
stable_budget      = 1500    # tokens for role + instructions (KV-cached)
dynamic_budget     = 2500    # tokens for date + memories (per-query)
history_budget     = 60000   # max conversation tokens in context
compact_threshold  = 0.85    # compact when history exceeds this fraction
compact_keep_turns = 6       # always keep N most recent turns
compact_strategy   = "lossless"   # archive old turns to memory before compressing
memory_min_score   = 0.25    # skip memories below this relevance score
memory_max_tokens  = 800     # hard cap on injected memories
auto_extract       = true    # regex-based fact extraction after each turn (no LLM calls)
                             # set false to disable automatic memory noise
```

**Environment variables:**

| Variable | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `GHOSTCLAW_PROVIDER` | Override provider name |
| `GHOSTCLAW_MODEL` | Override model name |
| `GHOSTCLAW_DATA_DIR` | Override data directory |
| `GHOSTCLAW_LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

## Providers

| Provider | How | Extra deps |
|---|---|---|
| `anthropic-raw` | `urllib` + JSON (default) | none |
| `anthropic-sdk` | Official `anthropic` library | `pip install ghostclaw[anthropic]` |
| `ollama` | Local HTTP `localhost:11434` | none (needs Ollama running) |
| `openai-raw` | `urllib` + JSON | none |

## Context Engine

GhostClaw uses a pluggable `ContextEngine` to manage the token lifecycle within each turn.

### Two-section system prompt

```
┌─────────────────────────────────────────────────────────┐
│ STABLE PREFIX  (Anthropic KV-cache eligible)            │
│  - Base role prompt (no date)                           │
│  - agent.instructions (your static rules / persona)     │
│  Token budget: context.stable_budget (default 1500)     │
├─────────────────────────────────────────────────────────┤
│ DYNAMIC SUFFIX  (per-query, always fresh)               │
│  - Today's date                                         │
│  - Relevant memories (score-gated, budget-capped)       │
│  Token budget: context.dynamic_budget (default 2500)    │
└─────────────────────────────────────────────────────────┘
```

### Lossless compaction

When conversation history exceeds `compact_threshold × history_budget`, the engine:
1. Takes all turns except the most recent `compact_keep_turns`
2. Calls the LLM once to produce a bullet-point summary (~1024 tokens output)
3. Saves the original turns to `MemoryStore` tagged `_compact_archive` (retrievable later)
4. Replaces the old portion with `[Compressed context]\n{summary}`
5. Emits a `{"type": "compaction", "archived": N, "kept": M}` event visible in the REPL

Unlike lossy compaction, old information is never permanently discarded.

### Automatic memory extraction

`DefaultContextEngine.after_turn()` runs lightweight regex patterns over each turn to extract facts (URLs, file paths, version strings, key=value pairs, names). Up to 3 facts per turn are saved with the `_auto_extract` tag. Zero LLM calls.

Disable with `context.auto_extract = false` in config. View auto-extracted memories with `/memories --auto` in the REPL; delete with `/forget <id>`.

### Custom ContextEngine

```python
from ghostclaw.context.engine import ContextEngine
from ghostclaw.agent import Agent

class MyEngine(ContextEngine):
    async def assemble(self, query, policy, memory, config, session_id=None):
        return "You are my custom agent.", f"Today: {__import__('datetime').date.today()}"

    async def compact(self, messages, policy, provider, model, memory, session_id):
        return messages[-policy.compact_keep_turns:]   # simplest possible compaction

    async def after_turn(self, session_id, user_input, assistant_response, memory):
        pass   # or do custom post-turn work

agent = Agent(context_engine=MyEngine())
```

`DefaultContextEngine(auto_extract=True)` — the `auto_extract` flag is wired automatically from `config.context.auto_extract` when `AgentLoop` constructs the default engine.

## Memory System

Notes are stored in two places simultaneously:

- **Markdown files** — `{data_dir}/notes/YYYY-MM-DD/{id}-{slug}.md` (human-readable)
- **SQLite** — `{data_dir}/memory.db` (FTS5 index + vector embeddings + session turns)

Search uses a hybrid strategy with lazy evaluation:

```
1. FTS search first
2. If max FTS score ≥ 0.8: skip vector search (saves embedding cost)
3. Otherwise: hybrid score = 0.6 × BM25(FTS5) + 0.4 × cosine(vector)
4. Filter: score ≥ memory_min_score (default 0.25)
5. Budget cap: stop at memory_max_tokens (default 800)
6. Session cache: same query within same session cached 30 s
```

Vector embeddings default to a local TF-IDF implementation (no model download). Optionally switch to Ollama `nomic-embed-text` or OpenAI embeddings via `[memory] embed_provider`.

The `turns` table stores `input_tokens` and `output_tokens` per turn. `ghostclaw sessions` shows per-session token totals.

**Memory tags used internally:**

| Tag | Meaning |
|---|---|
| `_compact_archive` | Turns archived during lossless compaction |
| `_auto_extract` | Facts extracted automatically by `after_turn()` |

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

`run_shell` is registered but **not in the default `tools.enabled` list**. Enable it deliberately:

```toml
[tools]
enabled = ["remember", "recall", "search_notes", "get_time", "platform_info", "run_shell"]
```

In the interactive REPL, `run_shell` always prompts for confirmation before executing. In `ghostclaw chat` and the WebSocket server, it executes directly (no prompt). A built-in deny-list blocks the most destructive patterns (`rm -rf /`, `mkfs`, `dd if=`, fork bombs, etc.).

Enable additional file/web tools:

```toml
[tools]
enabled = ["remember", "recall", "search_notes", "get_time", "platform_info",
           "read_file", "write_file", "list_dir", "fetch_url"]
```

## Plugin Tools

Drop any `.py` file into `~/.config/ghostclaw/tools/` (macOS/Linux). Functions decorated with `@tool` are auto-discovered at startup:

```python
from ghostclaw.tools.base import tool, ToolResult

@tool(name="my_tool", description="Does something useful")
def my_tool(query: str, limit: int = 5) -> ToolResult:
    return ToolResult.ok(f"Result for: {query}")
```

To add an interactive confirmation hook (like `run_shell` does):

```python
@tool(name="my_tool", description="...")
async def my_tool(command: str, _confirm_fn=None) -> ToolResult:
    if callable(_confirm_fn) and not _confirm_fn(command):
        return ToolResult.error("Cancelled by user.")
    # ... proceed
```

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

Agent tools available inside each agent's context (after `enable_agent_tools()`):

| Tool | Description |
|---|---|
| `delegate_to_agent` | Call a named agent with a subtask |
| `list_agents` | List all available agents |
| `broadcast_to_agents` | Call multiple agents in parallel |
| `run_pipeline` | Run a pipeline from within a tool call |

## WebSocket Server

```bash
ghostclaw serve [--host 0.0.0.0] [--port 8765]
```

Send JSON messages, receive streamed events:

```json
// Request types
{"type": "chat",     "text": "Hello", "agent": "default", "session_id": "s-123"}
{"type": "pipeline", "text": "Task",  "agents": ["researcher", "writer"]}
{"type": "ping"}

// Response events
{"type": "session",       "session_id": "s-..."}
{"type": "chunk",         "text": "..."}
{"type": "tool_call",     "tool": "recall", "input": {...}}
{"type": "tool_result",   "tool": "recall", "result": "..."}
{"type": "compaction",    "archived": 44, "kept": 6}
{"type": "pipeline_step", "agent": "researcher", "output": "..."}
{"type": "done",          "text": "...", "input_tokens": 120, "output_tokens": 45}
{"type": "error",         "message": "..."}
```

## Python API

```python
import asyncio
from ghostclaw.agent import Agent

agent = Agent()

# Single message
response = asyncio.run(agent.chat("What do you know about my projects?"))
print(response)

# Streaming single message
async def stream():
    loop = agent.new_loop()
    async for chunk in loop.stream_run("Tell me a story"):
        print(chunk, end="", flush=True)
asyncio.run(stream())

# Persistent REPL session
loop = agent.new_loop()
asyncio.run(loop.run("My name is Tuan"))
asyncio.run(loop.run("What is my name?"))

# Session token usage
print(f"In: {loop._session_input_tokens}  Out: {loop._session_output_tokens}")

# Debug state (history size, token counts, compact threshold)
state = loop.debug_state()
print(state["history_turns"], state["history_tokens"], state["history_budget"])

# Consume event stream (same as REPL does internally)
async def events():
    loop = agent.new_loop()
    async for event in loop.event_stream("Run the tests"):
        if event["type"] == "tool_call":
            print(f"Tool: {event['tool']}")
        elif event["type"] == "done":
            print(f"Tokens: {event['input_tokens']} in / {event['output_tokens']} out")
asyncio.run(events())

# Direct memory operations (no LLM)
agent.remember("GhostClaw uses Python 3.11+", title="Tech stack")
results = agent.search("Python")
notes = agent.list_memories(limit=20)           # recent notes (excludes _auto_extract)
notes = agent.list_memories(tag="_auto_extract") # auto-extracted facts only
agent.forget(notes[0]["note_id"])               # delete a note

# Custom ContextEngine
from ghostclaw.context.engine import DefaultContextEngine
agent = Agent(context_engine=DefaultContextEngine(auto_extract=False))

agent.close()
```

## Project Structure

```
ghostclaw/
├── pyproject.toml
├── Makefile
├── config/
│   └── ghostclaw.toml.example
├── ghostclaw/
│   ├── cli.py              # Entry point: REPL + subcommands
│   ├── agent.py            # High-level Agent class
│   ├── loop.py             # AgentLoop: ReAct + ContextEngine + event_stream
│   ├── gateway.py          # Multi-agent routing and session affinity
│   ├── server.py           # WebSocket server
│   ├── exceptions.py
│   ├── context/            # ContextEngine ABC + DefaultContextEngine + ContextPolicy
│   ├── config/             # tomllib loader, dataclass schema
│   ├── memory/             # SQLite FTS5 + vectors + Markdown
│   ├── providers/          # LLMProvider ABC + implementations
│   ├── tools/              # @tool decorator, registry, executor
│   │   └── builtins/       # memory, system, file, web, shell, agent tools
│   └── util/               # ids, token estimation, logging
└── tests/
```

## Development

```bash
# Run tests (67 total)
python -m pytest tests/ -v

# Install with all optional deps
pip install -e ".[all]"

# Syntax check
make lint

# Clean build artifacts
make clean
```

## Requirements

- Python 3.11+ (uses `tomllib` from stdlib)
- No mandatory third-party packages
- An API key for your chosen provider (or a running Ollama instance)
