# HushClaw

> A local-first AI agent runtime with persistent context, evolving personal memory, and inspectable evidence behind personalization.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org) [![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE) [![Zero deps](https://img.shields.io/badge/core%20deps-zero-brightgreen.svg)](#)

---

## What's New

The latest upgrade makes personal memory **inspectable and correctable**, while preserving existing conversations and files.

| Upgrade | What changes for you |
|---|---|
| **Per-answer understanding** | A compact **本次理解** entry shows your question, explicitly expressed viewpoints, historical references, and matching answer excerpts. Counts describe evidence—not a fabricated “understands you” percentage. |
| **Correctable personal context** | Confirm a useful record or stop referencing an inaccurate one through the shared confirmation dialog. Original conversations remain intact. |
| **Continuous context and evolving viewpoints** | Short follow-ups borrow the preceding question for retrieval; opinion updates can reuse an existing topic and retain refinement/reversal history. Current instructions take precedence over older inferences. |
| **More reliable recall** | Rank-based keyword/vector fusion, Chinese-aware tokenization, restart-stable local vectors, bounded query caching, and a repair command for old or mismatched indexes. |
| **Recoverable background learning** | Understanding review and per-turn learning use database-backed jobs with bounded retries and restart recovery, without holding up the completed answer. |
| **Topic-named Markdown downloads** | Downloads use a document title, then the session name, then a short question-derived name. Names are sanitized and length-limited; no extra model request is needed. |

These build on the existing compact chat UI: real runtime stages beside **Thinking**, an inline streaming cursor, file ratings/tags, and shared confirmation dialogs. See [Browser UI](#browser-ui) and [Upgrade Notes](#upgrade-notes) for details.

---

## Quick Start

```bash
# macOS / Linux
bash <(curl -fsSL https://raw.githubusercontent.com/CNTWDev/hushclaw/master/install.sh)

# Windows (PowerShell)
irm https://raw.githubusercontent.com/CNTWDev/hushclaw/master/install.ps1 | iex
```

The installer clones the repo, creates a venv, encrypts the local database with
SQLCipher, wires up PATH, and opens your browser. Existing plaintext databases
and their migration snapshots are upgraded in place with verified atomic
cutover. A setup wizard walks you through the first API key.

```bash
hushclaw serve                  # personal mode (default)
hushclaw                        # interactive REPL
```

No npm or build step is required for normal install/run. The released WebUI
ships prebuilt static assets inside the Python package. No Docker.

---

## Deployment

```bash
bash install.sh
bash install.sh --distro personal  # select the personal distribution explicitly
```

Local-first single-user assistant. Persistent data is stored on your device (`~/.local/share/hushclaw/` on Linux, `~/Library/Application Support/hushclaw/` on macOS, `%LOCALAPPDATA%\hushclaw\` on Windows). The server binds to loopback by default. Model and remote embedding requests send selected content to the configured providers; web tools and enabled connectors also contact external services. Local storage is not a promise of offline operation.

- WebUI at `http://localhost:8765/personal`
- Memory, skills, and config all local
- Upgrade in place: `bash install.sh --update`

---

## Why HushClaw?

HushClaw treats the agent as a long-lived collaborator: retain session continuity, select relevant personal context, and learn from outcomes and corrections. Personal records remain fallible; you can inspect their sources rather than taking a claim of “knowing you” on trust.

| | HushClaw |
|---|---|
| **Memory** | 4-dimensional: notes · user profile · domain beliefs · learning reflections |
| **Learning** | Background profile/opinion extraction, task reflection, and narrowly gated refinement of editable skills |
| **Context** | Stable/dynamic prompt split with Anthropic cache-control support; savings depend on model, cache eligibility and workload |
| **Personalization** | Source-backed references, literal answer correspondences, and explicit confirmation/correction |
| **Harness** | Session-frozen tool surface · on-demand long-tail tools · queryable per-run latency |
| **Install** | One command; zero-dependency embeddable core, secured runtime extras bundled by the installer |
| **UI** | Streaming chat, live execution stages, compact file management and understanding receipts; HTTP/WebSocket on one port |
| **Extensibility** | Drop a `.py` to add a tool. Drop a `.md` to add a skill pack. |

---

## Agent OS Architecture

HushClaw is moving toward an **Agent OS kernel + distro** model: one shared runtime kernel, multiple product distributions.

Today the default package is still bundled as:

```
hushclaw = Agent kernel + Personal distro + WebUI/CLI shell
```

The important boundary is already in place:

| Layer | Owns |
|---|---|
| **Kernel** | AgentLoop · ToolRegistry · ContextEngine · MemoryPort · Provider adapters · PolicyGate · AuditEvent |
| **Distro** | runtime profile · enabled tools/skills · policy rules · lifecycle hooks |
| **Shell** | CLI · WebUI · channel entrypoints · HTTP/WebSocket transport |
| **Infra** | local SQLite · model APIs · browser runtime · optional connector SDKs |

Product shells should enter through `DistroRuntime.build()` and `AgentOSService`, not construct kernel pieces directly. The supported packaged distro is `personal` (local-first, single user).

`storage_profile` is distro-declared but kernel-owned. `personal` uses `local_sqlite`.

### Harness v2: stable narrow waist

The registry can contain browser, connector, skill, and domain tools without
sending every schema to the model on every round. A new session freezes one
provider-facing tool surface:

- common tools remain directly callable;
- `tool_search` returns exact schemas for long-tail tools;
- `tool_call` invokes the selected underlying tool through the same policy,
  confirmation, audit, timeout, and verification path;
- the exact schema JSON is persisted for the session, so loop recreation,
  process restarts, and later skill installs do not invalidate its prompt prefix.

With the default browser-enabled registry, the current local benchmark exposes 21 of 72
registered tools and reduces estimated tool-schema input from 8,191 to 2,659
tokens (67.5%). This is an estimated schema-size reduction, not a measured reduction in total latency or model billing. Run `python3 scripts/bench_startup.py --json` to measure the active build.
Set `tools.discovery_mode = "all"` to expose every enabled tool, or `"bridge"`
to force discovery mode. `"auto"` switches when `schema_budget_tokens` is exceeded.

---

## Memory System

The Memories panel organizes durable records into four areas. These are evidence and working interpretations—not an infallible model of who you are:

### 1. Knowledge Base

Raw notes indexed at save time with semantic type:

| Type | What it captures |
|---|---|
| `interest` | Topics you keep returning to |
| `belief` | Opinions and principles you've stated |
| `preference` | How you like to work |
| `fact` | Technical facts, project context |
| `decision` | Choices already locked in |

Recall combines scoped BM25 and vector candidates with **reciprocal-rank fusion**, rather than mixing incompatible raw scores. Chinese text uses overlapping character bigrams. Local embeddings are deterministic across process restarts; a failed remote embedding request falls back to keyword retrieval, never writes a local vector under a remote model name. Query embeddings have a bounded 60-second cache.

### 2. User Profile

Structured facts extracted from your interaction patterns, organized by category:

```
Preferences · Communication style · Work habits · Focus areas · Ongoing goals · Avoidances
```

Relevant preferences and viewpoints are selected together within the context budget, with a per-category cap. Stable communication/workflow preferences remain eligible even when old; current instructions and current-session context take precedence. Short follow-ups also use the preceding user question for retrieval. Historical inferences are fallible, not instructions or proof of a user's motives.

### 3. Belief Models

Domain knowledge crystallized from accumulated signals. When you discuss a topic repeatedly, HushClaw synthesizes a coherent model — `latest` view, historical `entries`, `trajectory`, and `summary` — rather than just stacking raw notes.

Each model tracks how your understanding has evolved and marks itself `dirty` when new signals outpace the last consolidation.

Normal chat now retrieves the source-backed **opinion timeline** directly. Extraction can reuse a canonical topic ID when wording changes; refinement and reversal remain separate events with their stated reasons. This does not retroactively merge every older topic, and inferred reasons still need your confirmation.

### 4. Learning Reflections

After every complex task (3+ tool calls, errors, corrections, or skills used), the runtime reflects:

- **What worked** — outcome, lesson learned, strategy hint
- **What failed** — failure mode classification, corrective signal
- **Skill quality** — a heuristic outcome score based on corrections/errors/success, not a benchmark accuracy percentage

These accumulate into a searchable reflection log the agent uses to avoid repeating mistakes.

### Recall Tuning

Legacy/general recall exposes these tuning knobs; the personal-context selector uses relevance, category diversity and explicit feedback instead of random sampling:

```toml
[context]
memory_decay_rate     = 0.002  # Ebbinghaus half-life ~350 days
retrieval_temperature = 0.1    # softmax over recall candidates
serendipity_budget    = 0.10   # 10% of memory tokens = cross-domain wildcards
```

Default is `0.0` for all three — pure deterministic retrieval.

### Per-answer understanding receipts

Each new chat answer has a compact **本次理解** entry. Expand it to inspect the current question, explicitly expressed positions with original quotes, and the bounded personal memories supplied to that answer. A background review adds literal answer excerpts when it finds a meaningful correspondence.

- **参考** counts selected personal records; **对应** counts records with a validated answer excerpt. Neither is an accuracy percentage or proof of the model's internal reasoning.
- **符合我** confirms a record. **不准确** uses the shared confirmation dialog and excludes that record from subsequent personal-context retrieval; the original conversation stays intact and the exclusion can be reversed.
- Hidden/excluded/deleted source messages are not exposed through receipts. Older answers without a receipt are explicitly marked as unavailable, not reconstructed as if we knew what the model used.
- Receipts, feedback and learning jobs live in SQLite, covered by database encryption when enabled (as in one-click installs). Each queued item has up to **three attempts in total** and resumes after restart. The review uses the configured cheap model (or main model), normally adding one background model request per completed chat answer, separate from learning extraction. Retries add requests. It does not delay streaming completion. Selected question/answer excerpts and personal evidence go to that configured provider, just like chat context. Provider privacy policies still apply.

Counts cover the selected **personal-memory records**, not all session-history tokens, file attachments or tools used. Correspondence review is post-hoc and can make mistakes even when quoted spans are validated. A rejected record is excluded from personal-context selection; this does not erase its original wording from conversation history or guarantee removal of every duplicate inference. Views marked pending/failed are not presented as completed understanding.

### Upgrade Notes

Schema **10** is additive: it preserves existing conversations, notes, files and opinion histories. The encrypted database migrator creates a pre-upgrade backup. Restore the database backup together with the matching older code when rolling back; do not run old code against a newer schema.

To check and repair old, missing or dimension-mismatched vector indexes after upgrading:

```bash
python -m hushclaw.memory.reindex                 # report only
python -m hushclaw.memory.reindex --apply         # regenerate up to 1000 derived vectors
```

Run with the same environment/configuration as the server and an available embedding provider. Repeat if more than 1000 entries need repair. This replaces only derived indexes, not source notes or conversations; embeddings can always be regenerated. Remote embedding inputs are bounded to a 4000-character head/tail representation for oversized notes; full text remains available to keyword search. Existing personalized records are reused, but no bulk historical LLM re-analysis runs automatically.

---

## Learning System

Learning records are intended to improve future responses; quality depends on the model, source evidence and user corrections, and is not guaranteed to improve monotonically.

### After every turn

- A durable job can extract profile facts, opinion-evolution events and knowledge notes with the configured `agent.cheap_model`, falling back to `agent.model`. Input-length gates may skip extraction; this is **not** zero-LLM regex extraction.
- Explicit correction signals are retained, and receipt feedback influences subsequent personal-context selection.
- Understanding review is an additional background task. Failed review stays visibly pending/failed instead of being represented as successful personalization.

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
  Configured target: context.stable_budget   (application default 4000 tokens)

DYNAMIC SUFFIX ── rebuilt every query ──────────────────────────────────────
  Today's date · USER.md · working state · personal evidence · relevant session references
  Configured target: context.dynamic_budget  (application default 4000 tokens)
```

These are configuration targets, not a promise that all assembled prompt blocks are hard-truncated to that size. Personal memory has a separate `context.memory_max_tokens` selection budget; history has its own budget. Existing installations may retain different configured values. The low-level `ContextPolicy` constructor also has different defaults from the application configuration.

When history overflows, the active conversation uses a bounded continuity checkpoint:

| Strategy | Effect |
|---|---|
| `lossless` | Structured continuity summary plus an additional searchable archive |
| `summarize` / `abstractive` | Structured continuity summary; no additional archive. Abstract-only compression is no longer used for active conversations |
| `prune_tool_results` | Replaces older tool outputs with placeholders, retaining valid call/result pairs |

Recent original turns and current user corrections take precedence over older summaries,
working state, and cross-session memory. Durable turns/events remain the source of truth;
summaries are necessarily lossy aids, not a substitute for all session history.

Checkpoints record a retained-message boundary and a fingerprint of the covered prefix.
Restart restores **summary + the complete subsequent conversation**, scoped to the thread.
Changed/excluded history invalidates a checkpoint and falls back to original records.
Summary failure, empty output or truncation keeps the original context; oversized summary
inputs are processed in chunks rather than silently dropped. This can temporarily leave
history above its soft budget when the summary provider is unavailable.

Checkpoints were introduced in schema v9 and are retained in schema v10 (encrypted when database encryption
is enabled). The normal startup migration backs up an existing database before upgrading.
Legacy `summary.md` files and original records are preserved, but unbounded legacy summaries
are no longer used to replace conversation history. The next successful compaction creates
a verified checkpoint; the first long conversation after upgrading may therefore take longer.

---

## Browser UI

The chat surface keeps the answer central: collapsible navigation/session list on the left, streaming conversation in the middle, and a compact **Files** panel on the right.

| Feature | Behavior |
|---|---|
| **Live progress** | Thinking shows public runtime stages such as context preparation, model requests and tool execution, with a lightweight upward transition and reduced-motion support. These are execution signals, not private model reasoning or invented percentage progress. |
| **Readable streaming** | The cursor follows the streamed response. Detailed runtime diagnostics remain separate from the main answer. |
| **Understanding receipts** | A one-line, expandable entry distinguishes retrieved personal records from answer correspondences and lets you inspect/correct evidence. |
| **Files** | Imported/generated files support search, five-star ratings, tags, importance filtering, sorting, attachment and deletion with shared confirmation. Deletion also removes the managed local file, not just the list entry. |
| **Message actions** | Markdown download, copy, image export, print/PDF and message deletion confirmation. Markdown preserves Q&A context and attribution; its filename is derived locally from the document title, session title or question, sanitized for filesystem safety and length-limited. |
| **Updates** | A new WebUI cache version prompts before reloading, so an update does not silently interrupt your draft. |

**Panels:** Chat · Agents · Skills · Connections · Memories · Tasks · Calendar · Logs · Settings. Channel and connector availability depends on configuration and installed extras.

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

**Custom tools** — drop a `.py` in the configuration directory's `tools/` folder: `~/.config/hushclaw/tools/` on Linux, `~/Library/Application Support/hushclaw/tools/` on macOS, or `%APPDATA%\hushclaw\tools\` on Windows (unless `tools.plugin_dir` overrides it):

```python
from hushclaw.tools.base import tool, ToolResult

@tool(name="my_tool", description="Does something useful")
def my_tool(query: str) -> ToolResult:
    return ToolResult.ok(f"Result: {query}")
```

**Skill packs** — a Git repo with `SKILL.md` + optional `tools/*.py`. Install from the Skills panel or CLI.

**Providers** — `anthropic-raw` (default, no deps) · `anthropic-sdk` · `openai-raw` · `openai-sdk` · `gemini` · `ollama`, plus compatible proxy adapters. SDK providers require their corresponding optional dependency; configure an endpoint/model supported by the selected adapter.

---

## Config

`~/Library/Application Support/hushclaw/hushclaw.toml` (macOS) · `~/.config/hushclaw/hushclaw.toml` (Linux) · `%APPDATA%\hushclaw\hushclaw.toml` (Windows):

```toml
[provider]
name = "anthropic-raw"
# api_key = "sk-..."  # or ANTHROPIC_API_KEY env var

[agent]
model = "claude-sonnet-4-6"
# cheap_model = "..."  # optional: a model supported by the same configured provider

[context]
compact_strategy      = "lossless"
memory_decay_rate     = 0.0   # > 0 enables Ebbinghaus decay
serendipity_budget    = 0.0   # > 0 enables cross-domain recall wildcards

[memory]
database_encryption   = "sqlcipher"  # default for one-click installs
```

## Local Data Security

One-click installs encrypt the complete SQLite database—including messages,
memories, tasks, file metadata, and search indexes—with SQLCipher. The 256-bit
database key is kept in macOS Keychain, Windows current-user DPAPI, or Linux
Secret Service. On a headless machine where no credential vault is available,
HushClaw uses an owner-only key file and reports the fallback so it is never
mistaken for equivalent protection.

```bash
hushclaw database status       # encryption, key source, schema and integrity
hushclaw database harden       # repair owner-only filesystem permissions
hushclaw database encrypt      # idempotent migration for a developer install
hushclaw database recovery-key # sensitive: copy once and keep offline
hushclaw doctor
```

Keep the recovery key in a password manager or offline vault. HushClaw backup
archives preserve SQLCipher encryption and deliberately exclude device-local
key files. On another machine, `backup import` securely prompts for the recovery
key; non-interactive automation can pipe it with `--database-key-stdin` or set
`HUSHCLAW_DATABASE_KEY`.

This protects files at rest from casual inspection and ordinary SQLite tools.
SQLCipher encrypts the database, **not every file in the application directory**:
imported/generated documents, Markdown exports, logs and configuration files
need their own access controls or full-disk encryption. A backup ZIP is not an
encrypted container merely because its database entry is encrypted.
It cannot protect data after HushClaw has unlocked it from malware controlling
your login session, process-memory inspection, or a compromised OS. FileVault,
BitLocker, or LUKS is still recommended for whole-device protection.

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

By default the archive includes your `hushclaw.toml`, local data directory, and
custom tools from the config directory. If the source database is encrypted,
the archived database remains encrypted and no key material is included. Use
`--data-dir` during import if you want to restore into a different location on
the new machine; enter the offline recovery key when prompted.

---

## Install Options

```bash
# Installer flags
bash install.sh                        # install and start
bash install.sh --distro personal      # select personal mode explicitly
bash install.sh --update               # pull latest, restart
bash install.sh --update --skill-preserve-local # preserve locally edited bundled skills
bash install.sh --stop                 # stop running server
bash install.sh --uninstall            # remove (prompts about data)
bash install.sh --uninstall --purge    # remove everything including data

# Developer install
git clone https://github.com/CNTWDev/hushclaw.git && cd hushclaw
pip install -e ".[server,encryption]" # server + SQLCipher database support
pip install -e ".[all]"       # everything (browser, web fetch, all extras)

# Run tests
python -m pip install pytest
python -m pytest tests/ -v
# Frontend behavior tests (Node.js required for development only)
node --experimental-vm-modules --test tests/test_*.mjs
```

On Windows, use `.\install.ps1 -Update` to upgrade the installed runtime. After a WebUI update, accept its reload prompt to activate the new cached assets. Review/commit local checkout changes before upgrading; do not use overwrite options merely to bypass a dirty working tree. The installer update defaults to refreshing official bundled skills; use the preserve-local option when retaining your edits is more important.

**Requirements:** Python 3.11+ · no mandatory third-party packages for the
embeddable core · API key for the chosen provider (or a running Ollama
instance). The one-click installer adds WebSocket, calendar, and SQLCipher
runtime extras automatically.
