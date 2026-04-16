# HushClaw

> A persistent, token-first AI agent runtime that learns your projects, preserves working state, and gets more useful the longer it runs.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org) [![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE) [![Zero deps](https://img.shields.io/badge/core%20deps-zero-brightgreen.svg)](#)

---

## Why HushClaw?

Most agent frameworks force a tradeoff:
- lightweight but forgetful
- powerful but operationally heavy
- useful in one interface, brittle everywhere else

HushClaw is designed as a long-lived agent system, not a single-session chatbot wrapper.

It keeps the runtime small, the context disciplined, and the memory durable:
- it runs as a pure-Python core with zero mandatory third-party dependencies
- it remembers users and projects through hybrid memory instead of replaying raw logs forever
- it compacts aggressively without dropping the thread, using summaries, lineage, and preserved working state
- it stays usable across browser UI, CLI, scheduled tasks, and messaging connectors from one shared runtime

| | HushClaw |
|---|---|
| **Install** | One `curl` command, zero npm, zero build step |
| **Run** | Pure Python 3.11 stdlib — `sqlite3`, `asyncio`, `tomllib`, `urllib` |
| **UI** | Full browser interface served from the same port as the WebSocket API |
| **Memory** | Hybrid FTS5 + local vector search + Ebbinghaus decay + serendipity budget |
| **Cost** | Anthropic KV-cache on the stable prefix — up to 75% input token cost reduction |
| **Extensibility** | Drop a `.py` file to add a tool. Drop a `.md` to add a skill pack. |

---

## What HushClaw Is

HushClaw is not an IDE-tethered copilot and not a thin shell around one model API.

It is a persistent agent runtime:
- it serves a full browser UI and WebSocket API from the same process
- it keeps session history searchable across runs
- it tracks compaction lineage so compressed conversations stay inspectable
- it preserves active working state so long-running tasks do not lose the plot
- it supports multi-agent routing, scheduled tasks, and messaging connectors without changing the core runtime model

The design goal is simple: an agent that stays cheap to run, easy to inspect, and more capable after weeks of use than it was on day one.

---

## Architecture

HushClaw keeps the architecture intentionally small. The runtime is split by responsibility, not by framework layer:

- `loop.py` is the core runtime. It owns one turn of work: assemble context, call the model, execute tools, compact history, and persist the result.
- `context/` owns context lifecycle only. It decides what enters the prompt and how old turns are compacted.
- `memory/` owns durable storage and retrieval. It should not know about WebSocket protocols or UI state.
- `server_impl.py` is the protocol edge. It translates WebSocket messages into gateway/runtime calls and should stay thin.
- `gateway.py` owns multi-agent routing, session affinity, and orchestration between agents.
- `agent.py` is wiring code. It assembles provider, memory, tools, skills, hooks, and loops, but should not become a second runtime.

The design rule is simple:
- keep the core runtime independent
- keep the edge layers thin
- prefer a few explicit modules over many abstract service layers
- only extract helpers when they remove real duplication or coupling

This means some large files are acceptable when they still represent one clear responsibility. HushClaw optimizes for inspectability and low operational weight over maximal decomposition.

---

## Quick Start

```bash
# macOS / Linux
bash <(curl -fsSL https://raw.githubusercontent.com/CNTWDev/hushclaw/master/install.sh)

# Windows (PowerShell)
irm https://raw.githubusercontent.com/CNTWDev/hushclaw/master/install.ps1 | iex
```

The installer clones the repo, creates a venv, wires up PATH, and opens your browser. A setup wizard walks you through the first API key. Done.

```bash
hushclaw          # interactive REPL
hushclaw serve    # browser UI at http://localhost:8765
```

---

## Browser UI

No React. No webpack. No CDN. A single `index.html` + vanilla JS modules served directly by the Python server.

```
┌── HushClaw ──────────────────────────────────────────────────────────┐
│  Chat · Agents · Memories · Skills · Tasks · Channels · ⚙ Settings  │
├──────────────────────────┬───────────────────────────────────────────┤
│ Sessions                 │  Streaming chat · collapsible tool calls  │
│  ┌─ Research (3 turns)   │                                           │
│  ├─ Code review          │  [recall] → searching memories…           │
│  └─ Morning brief        │  ✓ Found 4 relevant notes                 │
│                          │                                           │
│  Workspace ▾             │  Today I noticed you keep asking about    │
│                          │  async patterns — here's a pattern I      │
│                          │  think matches your mental model…         │
├──────────────────────────┴───────────────────────────────────────────┤
│  @researcher What's the latest on RISC-V?         [Export] [↑ Send]  │
│  session: a3f…8b  ● connected  In: 1,234  Out: 89  ~$0.0006          │
└──────────────────────────────────────────────────────────────────────┘
```

**Panels:** Chat with searchable session history · Agent builder · Memories search & management · Skill pack installer · Todo + scheduled tasks · Platform channel config (Telegram / Discord / Feishu / Slack / DingTalk / WeCom)

---

## Memory That Understands You

Most memory systems save *what happened*. HushClaw saves *who you are*.

Every note is classified at save time:

| `note_type` | What it captures | Example |
|---|---|---|
| `interest` | Topics you keep asking about | *"why does the upgrade deadlock?"* |
| `belief` | Opinions and principles you've stated | *"system tools shouldn't have delete buttons"* |
| `preference` | How you like to work | *"I want concise, evidence-first answers"* |
| `fact` | Technical facts and project context | *"this API returns USD, not cents"* |
| `decision` | Choices already made | *"we picked SQLite over Postgres"* |
| `action_log` | What the agent did (not recalled) | suppressed from context injection |

Recall is hybrid and lazy: **FTS5 (BM25) first** — if the top score exceeds 0.8, skip vector search entirely. Otherwise blend 60% BM25 + 40% cosine. Score-gate. Budget-cap. Cache for 30 s.

### Creativity Engine

Three biologically-inspired knobs to tune creative vs. deterministic recall:

```toml
[context]
memory_decay_rate     = 0.03   # Ebbinghaus: half-life ~23 days
retrieval_temperature = 0.3    # softmax sampling over candidates
serendipity_budget    = 0.15   # 15% of memory tokens = random cross-domain notes
```

Set all three to `0.0` (default) for pure deterministic retrieval.

---

## Token-First Context Engine

The system prompt is split in two — only the dynamic half changes per query:

```
STABLE PREFIX  ──── KV-cache eligible (Anthropic cache_control) ────────────
  Role · agent instructions · AGENTS.md · SOUL.md
  Budget: context.stable_budget   (default 1500 tokens)

DYNAMIC SUFFIX ──── rebuilt every query ─────────────────────────────────────
  Today's date · USER.md · score-gated recalled memories
  Budget: context.dynamic_budget  (default 2500 tokens)
```

When history exceeds `compact_threshold × history_budget`, the engine compacts — three strategies:

| Strategy | Effect |
|---|---|
| `lossless` | Archives raw turns to SQLite, then summarizes — nothing is lost |
| `summarize` | Summarizes and discards; smaller footprint |
| `abstractive` | Extracts transferable patterns and principles only, no verbatim facts |

Recent turns do not just collapse into a summary blob. HushClaw also preserves:
- session lineage — so compaction events remain inspectable
- active working state — goal, progress, open loops, and recent tool outputs
- resumable session history — so older conversations can be searched and continued later

---

## Providers

| Provider | Transport | Extra deps |
|---|---|---|
| `anthropic-raw` | `urllib` + JSON (default) | none |
| `anthropic-sdk` | Official SDK | `pip install hushclaw[anthropic]` |
| `openai-raw` | `urllib` + JSON | none |
| `ollama` | Local HTTP `localhost:11434` | none |
| `aigocode-raw` | Anthropic-compatible proxy | none |

---

## Multi-Agent

```toml
[[gateway.agents]]
name = "researcher"
tools = ["recall", "fetch_url"]

[[gateway.agents]]
name = "writer"
tools = ["remember"]

[gateway.pipelines]
research_write = ["researcher", "writer"]
```

```bash
hushclaw agents pipeline "researcher,writer" "Write a report on RISC-V"
```

Or in chat: `@researcher @writer parallel broadcast` · `@writer single agent` · no mention → default routing.

---

## Plugin Tools

```python
# Drop in ~/.config/hushclaw/tools/my_tool.py
from hushclaw.tools.base import tool, ToolResult

@tool(name="my_tool", description="Does something useful")
def my_tool(query: str, limit: int = 5) -> ToolResult:
    return ToolResult.ok(f"Result for: {query}")
```

Auto-discovered at startup. No registration, no config changes needed.

---

## Python API

```python
from hushclaw.agent import Agent

agent = Agent()

# Single message
response = agent.chat("What do you know about my projects?")

# Event stream (same protocol as WebSocket UI)
async for event in agent.new_loop().event_stream("Run the tests"):
    if event["type"] == "tool_call":
        print(f"→ {event['tool']}")
    elif event["type"] == "done":
        print(f"  In: {event['input_tokens']} / Out: {event['output_tokens']} tokens")

# Memory
agent.remember("Prefers concise answers", title="Style preference")
results = agent.search("preferences")
agent.close()
```

---

## Install Options

```bash
# Developer install
git clone https://github.com/CNTWDev/hushclaw.git && cd hushclaw
pip install -e ".[server]"    # core + WebSocket server
pip install -e ".[all]"       # everything

# Tests
python -m pytest tests/ -v    # 128 tests
```

**Key config** (`~/.config/hushclaw/hushclaw.toml` on Linux, `~/Library/Application Support/hushclaw/` on macOS):

```toml
[provider]
name = "anthropic-raw"
# api_key = "sk-..."  # or set ANTHROPIC_API_KEY

[agent]
model = "claude-sonnet-4-6"

[context]
compact_strategy  = "lossless"
memory_decay_rate = 0.0        # set > 0 to enable Ebbinghaus decay
serendipity_budget = 0.0       # set > 0 for cross-domain recall
```

---

## Requirements

- Python 3.11+
- No mandatory third-party packages
- An API key for your chosen provider (or a running Ollama instance)
- `websockets>=12.0` for `hushclaw serve` (installed automatically)
