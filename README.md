# HushClaw

> A persistent AI agent runtime that learns who you are, not just what you said.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org) [![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE) [![Zero deps](https://img.shields.io/badge/core%20deps-zero-brightgreen.svg)](#)

---

## Quick Start

```bash
# macOS / Linux
bash <(curl -fsSL https://raw.githubusercontent.com/CNTWDev/hushclaw/master/install.sh)

# Windows (PowerShell)
irm https://raw.githubusercontent.com/CNTWDev/hushclaw/master/install.ps1 | iex
```

The installer clones the repo, creates a venv, wires up PATH, and opens your browser. A setup wizard walks you through the first API key.

```bash
hushclaw serve    # browser UI at http://localhost:8765
hushclaw          # interactive REPL
```

No npm. No build step. No Docker. Pure Python 3.11+.

---

## Why HushClaw?

Most agent tools are forgetful by design — each session starts from zero, every preference has to be re-explained, every project context has to be re-established.

HushClaw treats the agent as a long-lived collaborator, not a stateless API wrapper. The longer it runs, the more it knows — about you, your domain, your work patterns, and its own past mistakes.

| | HushClaw |
|---|---|
| **Memory** | 4-dimensional: notes · user profile · domain beliefs · learning reflections |
| **Learning** | Self-improves per turn: reflects on outcomes, patches skills, updates your model |
| **Context** | Stable/dynamic split with Anthropic KV-cache — up to 75% input token savings |
| **Install** | One `curl` command, zero mandatory deps, pure Python stdlib core |
| **UI** | Full browser interface from the same port as the WebSocket API |
| **Extensibility** | Drop a `.py` to add a tool. Drop a `.md` to add a skill pack. |

---

## Memory System

Most memory systems save *what happened*. HushClaw saves *who you are* — across four persistent dimensions visible in the Memories tab:

### 1. 知识库 — Knowledge Base

Raw notes indexed at save time with semantic type:

| Type | What it captures |
|---|---|
| `interest` | Topics you keep returning to |
| `belief` | Opinions and principles you've stated |
| `preference` | How you like to work |
| `fact` | Technical facts, project context |
| `decision` | Choices already locked in |

Recall is hybrid and lazy: **BM25 first** — if the top score exceeds 0.8, skip vector search entirely. Otherwise blend 60% BM25 + 40% cosine. Score-gate → budget-cap → 30 s session cache.

### 2. 用户画像 — User Profile

Structured facts extracted from your interaction patterns, organized by category:

```
偏好 · 沟通风格 · 工作习惯 · 关注领域 · 常驻目标 · 避免事项
```

These are injected into every prompt as part of the dynamic context suffix — the agent always starts with a current picture of who it's talking to.

### 3. 领域认知 — Belief Models

Domain knowledge crystallized from accumulated signals. When you discuss a topic repeatedly, HushClaw synthesizes a coherent model — `latest` view, historical `entries`, `trajectory`, and `summary` — rather than just stacking raw notes.

Each model tracks how your understanding has evolved and marks itself `dirty` when new signals outpace the last consolidation.

### 4. 学习反思 — Learning Reflections

After every complex task (3+ tool calls, errors, corrections, or skills used), the runtime reflects:

- **What worked** — outcome, lesson learned, strategy hint
- **What failed** — failure mode classification, corrective signal
- **Skill quality** — 0–100% score per skill used, driving auto-improvement

These accumulate into a searchable reflection log the agent uses to avoid repeating mistakes.

### Recall Tuning

Three knobs to trade determinism for creativity:

```toml
[context]
memory_decay_rate     = 0.002  # Ebbinghaus half-life ~350 days
retrieval_temperature = 0.1    # softmax over recall candidates
serendipity_budget    = 0.10   # 10% of memory tokens = cross-domain wildcards
```

Default is `0.0` for all three — pure deterministic retrieval.

---

## Learning System

HushClaw gets more capable after each session, not just more familiar.

### After every turn
- Lightweight regex fact extraction (zero LLM calls) — interests, beliefs, preferences, decisions auto-extracted and tagged
- Correction signals detected and stored (negative feedback shapes future behavior)

### After complex tasks
- **Trace reflection** — tool call sequence analyzed for success/failure patterns
- **Profile updates** — structured user profile facts written or confidence-updated
- **Skill auto-patch** — single editable skills refined on strong quality signals
- **Skill outcomes** — per-skill quality scores accumulated across sessions

### Belief consolidation
When accumulated belief signals exceed a threshold, the runtime consolidates raw entries into a coherent domain model using an LLM call — without you ever asking it to.

---

## Token-First Context Engine

The system prompt is split so the expensive half is cache-eligible:

```
STABLE PREFIX  ── KV-cache (Anthropic cache_control) ──────────────────────
  Role · AGENTS.md · SOUL.md
  Budget: context.stable_budget   (default 1500 tokens)

DYNAMIC SUFFIX ── rebuilt every query ──────────────────────────────────────
  Today's date · USER.md · score-gated recalled memories
  Budget: context.dynamic_budget  (default 2500 tokens)
```

When history overflows, three compaction strategies:

| Strategy | Effect |
|---|---|
| `lossless` | Archives raw turns to SQLite before summarizing — nothing lost |
| `summarize` | Summarizes and discards; smaller footprint |
| `abstractive` | Extracts transferable patterns only, no verbatim facts |

Compaction preserves session lineage and active working state — the agent never "loses the plot" mid-task.

---

## Browser UI

```
┌── HushClaw ──────────────────────────────────────────────────────────┐
│  Chat · Agents · Memories · Skills · Tasks · Channels · ⚙ Settings  │
├──────────────────────────┬───────────────────────────────────────────┤
│ Sessions                 │  Streaming chat · collapsible tool calls  │
│  ┌─ Research (3 turns)   │                                           │
│  ├─ Code review          │  [recall] → searching memories…           │
│  └─ Morning brief        │  ✓ 4 relevant notes                       │
│                          │                                           │
│  Workspace ▾             │  Memories ▾                               │
│                          │  知识库 · 用户画像 · 领域认知 · 学习反思    │
├──────────────────────────┴───────────────────────────────────────────┤
│  @researcher What's the latest on RISC-V?         [Export] [↑ Send]  │
└──────────────────────────────────────────────────────────────────────┘
```

**Panels:** Chat + session history · Agent builder · Memories (4 sub-tabs) · Skill pack installer · Todo + scheduled tasks · Calendar · Platform channels (Telegram / Discord / Slack / Feishu / DingTalk / WeCom)

---

## Multi-Agent

```toml
[[gateway.agents]]
name = "researcher"
tools = ["recall", "fetch_url"]

[[gateway.agents]]
name = "writer"
tools = ["remember"]
```

```bash
hushclaw agents pipeline "researcher,writer" "Write a report on RISC-V"
```

Or in chat: `@researcher @writer` for parallel broadcast · `@writer` for single agent · no mention → default routing.

---

## Extensibility

**Custom tools** — drop a `.py` in `~/.config/hushclaw/tools/`:

```python
from hushclaw.tools.base import tool, ToolResult

@tool(name="my_tool", description="Does something useful")
def my_tool(query: str) -> ToolResult:
    return ToolResult.ok(f"Result: {query}")
```

**Skill packs** — a Git repo with `SKILL.md` + optional `tools/*.py`. Install from the Skills panel or CLI.

**Providers** — `anthropic-raw` (default, no deps) · `anthropic-sdk` · `openai-raw` · `ollama` · Anthropic-compatible proxies.

---

## Config

`~/Library/Application Support/hushclaw/hushclaw.toml` (macOS) · `~/.config/hushclaw/hushclaw.toml` (Linux):

```toml
[provider]
name = "anthropic-raw"
# api_key = "sk-..."  # or ANTHROPIC_API_KEY env var

[agent]
model = "claude-sonnet-4-6"

[context]
compact_strategy      = "lossless"
memory_decay_rate     = 0.0   # > 0 enables Ebbinghaus decay
serendipity_budget    = 0.0   # > 0 enables cross-domain recall wildcards
```

## Backup & Migration

Create a portable backup archive before moving to a new machine:

```bash
hushclaw backup export
hushclaw backup export ~/Desktop/hushclaw-backup.zip
```

Restore it on the new machine:

```bash
hushclaw backup import ~/Desktop/hushclaw-backup.zip
```

By default the archive includes your `hushclaw.toml`, local data directory, and custom tools from the config directory. Use `--data-dir` during import if you want to restore into a different location on the new machine.

---

## Install Options

```bash
# Developer install
git clone https://github.com/CNTWDev/hushclaw.git && cd hushclaw
pip install -e ".[server]"    # core + WebSocket server
pip install -e ".[all]"       # everything (browser, web fetch, all extras)

# Run tests
python -m pytest tests/ -v
```

**Requirements:** Python 3.11+ · no mandatory third-party packages · API key for chosen provider (or a running Ollama instance) · `websockets>=12.0` auto-installed with `[server]`
