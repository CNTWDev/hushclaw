# HushClaw · Technical reference

[← 返回项目首页](../README.md) · [VoxNexus 登录与模型接入](voxnexus.md)

Implementation details, advanced configuration, memory behavior and migration notes. For the default personal experience, start with the VoxNexus account centre described in the README.

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

`local` 是零依赖的词频哈希，不能理解没有共同词的同义表达。可选的 `fastembed` 使用本地中文神经模型，安装 `pip install 'hushclaw[memory-local]'`，然后设置 `[memory] embed_provider = "fastembed"`；默认模型为 `BAAI/bge-small-zh-v1.5`，首次使用时会下载权重。改换模型后执行下文的 `python -m hushclaw.memory.reindex --apply`，直到报告的 candidates 为 0；切回旧模型也需要重建派生向量。原始笔记和会话不受重建影响。当前检索在 1000 条合成笔记、本机哈希后端上测得 p95 约 4 ms；这不代表真实语料或回答质量。

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

File links in chat now tolerate whitespace inside Markdown destinations. Plain `/files/…` paths are linked after Markdown parsing, so existing links, references and code examples are not rewritten into nested links and incorrectly marked `[blocked]`. URL safety checks remain enabled; existing stored replies benefit on reload without regenerating their files.

Schema **12** is additive: it preserves existing conversations, notes, files, opinion histories and feedback, and adds a small calendar-source preferences table in the same database. The encrypted database migrator creates a pre-upgrade backup. Restore the database backup together with the matching older code when rolling back; do not run old code against a newer schema.

### Mail and calendar authorization — no personal developer account required

**Settings → Integrations** now explains the actual credential required by each provider and links to its official setup instructions. Selecting a preset fills server addresses; it does not authorize an account. Connection tests authenticate only, select the mailbox read-only, and never send a test email. Blocking network checks run outside the server event loop with timeouts.

| Service | Standalone mail setup | Calendar setup |
| --- | --- | --- |
| Gmail / Google | Enable 2-Step Verification and generate a [16-character app password](https://support.google.com/mail/answer/185833). Some organization / Advanced Protection policies disallow this. Never use the Google login password. | On Mac, add Google in Internet Accounts and enable Calendar, then authorize HushClaw's native-calendar helper. Gmail app passwords do **not** authorize Google CalDAV. |
| Outlook / Microsoft 365 | Requires [OAuth / Modern Authentication](https://support.microsoft.com/zh-cn/outlook/pop-imap-and-smtp-settings-for-outlook-com). The password-only IMAP form deliberately does not offer a working-login claim. On Mac, sign in through system Mail. | Use the Mac's Microsoft / Exchange calendar account, subject to organization policy. |
| iCloud | Full iCloud email address + [Apple app-specific password](https://support.apple.com/102525). | Prefer the system calendar on Mac; otherwise iCloud CalDAV with an app-specific password. |
| QQ / personal 163 | Enable IMAP/SMTP in webmail and generate its client authorization code, not the login password. Enterprise / 126 / other domains need their own endpoints. | Mail authorization does not imply a calendar service; use the provider's documented calendar source separately. |
| Zoho | Enable IMAP on a supported plan; MFA / SAML accounts use app passwords. Verify the [data-center and organization-specific servers](https://www.zoho.com/mail/help/imap-access.html). | Use a separately supported calendar integration; mail credentials alone do not authorize it. |
| Feishu Mail | Administrator must allow third-party clients; generate a dedicated password in desktop Feishu → Settings → Email → third-party client login. | Get calendar-specific CalDAV credentials from the calendar integration settings when supported by the organization. |

For **Fastmail / Nextcloud CalDAV**, use the provider's HTTPS calendar address and appropriately scoped app password; plan / administrator restrictions still apply. IMAP verifies TLS certificates. SMTP port **465 uses implicit TLS**, while other SMTP ports require STARTTLS; no plaintext credential fallback. Existing plaintext SMTP setups must migrate to TLS. Tests and mail tools share this transport policy. Saved passwords are reused only for an exact endpoint/user match, not the first account or a reordered account index.

On Mac, **管理本机日历来源** opens the existing explicit permission-and-selection flow; it never extracts system passwords or tokens. System Mail tools such as `macos_list_emails` must separately be added to `[tools].enabled`, with the macOS Automation permission granted by the user; they are not silently enabled by a mail preset. Other operating systems cannot reuse Mac accounts. For read-only use enable only read tools, not `macos_send_email`.

**OAuth has a publisher responsibility:** even a standalone app needs a registered OAuth client to offer its own Google / Microsoft sign-in. End users need not each register an app if the publisher supplies one, but this repository does not ship a working hosted OAuth service or borrow another application's client ID. The old nonfunctional broker default is disabled; managed Connect is unavailable until a real HTTPS broker is explicitly configured. Advanced custom Google OAuth remains available. New Google connector defaults request only `calendar.readonly`, not Gmail or Drive access. Existing grants/scopes are preserved; revoke and reauthorize with narrower scopes if desired. User sign-in, app-password generation and OS permission approval remain explicit user actions, never automatic installation steps.

### My itinerary — local-first calendar

Calendar now opens as a compact **day timeline**, with week, month and 30-day agenda views. It shows the current time, overlapping events side by side, and time conflicts / the largest unscheduled working-hours gap in the current filter. These are calendar-derived hints, not claims about your actual availability. Local events use the shared detail/edit dialogs and deletion confirmation; imported events are read-only.

On **macOS**, choose **日历来源 → 授权读取本机日历**, confirm the system permission, then select calendars and save. This reads calendars already visible in the Mac Calendar app, including synced accounts, without a separate Google OAuth connection. The service must run on that Mac: a remote Linux server cannot read your laptop's calendar. Google / CalDAV remain independent optional sources; hide duplicated sources if the same account is also synced by macOS.

- A small, locally built **EventKit helper** performs read-only access outside the harness kernel. Apple calls the read permission “full access”; the helper has no event-write APIs. Installation builds it when Apple Command Line Tools are available but **never requests permission or enables import automatically**. If needed, install the tools with `xcode-select --install` and retry from Calendar sources.
- Only selected calendars are imported: title, time, location and source; not event notes or attendees. The snapshot covers **90 days back / 275 days ahead**, refreshing every five minutes. A failed refresh keeps the previous snapshot. Disabling synchronization clears that source's imported copies; display filters only hide events. Permission can be revoked in macOS System Settings → Privacy & Security → Calendars.
- In **Settings → Integrations**, even the last CalDAV account can be removed. Confirm deletion or disabling, then **Save**: the sync worker is stopped before imported events and sync cursors are cleared. External originals, manually created events, and other source types are untouched. Re-enabling imports a fresh snapshot. Currently the first configured CalDAV account is the effective sync account.
- Preferences and imported events use the existing database (encrypted when enabled). Merely viewing or syncing does **not** invoke an LLM. **准备这场行程** fills a chat draft only; you choose whether to send it. Calendar data explicitly requested through chat tools is governed by the normal model-provider privacy boundary.
- Non-macOS installations keep local events and configured Google / CalDAV sources; no mandatory native dependency is added. Local-calendar access does not repair a misconfigured external OAuth broker.

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

Checkpoints were introduced in schema v9 and are retained in later schemas (encrypted when database encryption
is enabled). The normal startup migration backs up an existing database before upgrading.
Legacy `summary.md` files and original records are preserved, but unbounded legacy summaries
are no longer used to replace conversation history. The next successful compaction creates
a verified checkpoint; the first long conversation after upgrading may therefore take longer.

---

## Browser UI

### Reply evaluation and explicit viewpoint memory

Use **评价 · 摘记** below an assistant reply, or select a passage for the compact excerpt toolbar. The shared dialog supports:

- **Reply helpfulness:** 0–5 stars (0 clears the rating), optional reasons and comments. A high score does not endorse every claim or certify factual accuracy.
- **Fragment feedback:** inspiring, endorsed or disputed; optional summary, rationale and applicability/exception conditions. Saving as a reference is not agreement. Saving as your viewpoint or method requires explicit endorsement.
- **Scope and control:** current workspace by default, otherwise current session; cross-project use is opt-in. Revision-checked updates, withdrawal and restoration preserve history. Hidden, excluded, changed or purged sources stop contributing; purging the source also removes its feedback and revision copies.
- **Action assessment:** “评估观点与行动价值” prepares a normal chat draft covering evidence, applicability, benefit, effort, risk and a minimal validation step. You review and send it; feedback itself never creates or authorizes a task.

Architecture stays small: `WebUI → AgentOSService → MessageFeedbackStore → existing SQLite`. The existing personalization assembler reads relevant explicit records; AgentLoop, tools, providers and the background-job lifecycle are unchanged. Ratings are stored for review, not silently converted into model training or broad user preferences. Automatic extraction is still a fallible, unconfirmed inference, even with a matching user quotation. Excerpt anchoring accepts exact source text and simple Markdown emphasis/whitespace differences; complex rendered selections may require a shorter passage or original Markdown.

The chat surface keeps the answer central: collapsible navigation/session list on the left, streaming conversation in the middle, and a compact **Files** panel on the right.

| Feature | Behavior |
|---|---|
| **Live progress** | A single aligned status row shows the current runtime stage and disappears when the answer starts. A cumulative timer appears after 10 seconds. Completed tool traces are omitted from the conversation, including restored history. Developer mode adds an execution-details shortcut to the Runtime panel and input/result previews there. These are execution signals, not private model reasoning or invented percentage progress. |
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

**Advanced CLI/library providers** — `voxnexus` (default personal gateway) · `anthropic-raw` (no deps) · `anthropic-sdk` · `openai-raw` · `openai-sdk` · `gemini` · `ollama`, plus compatible proxy adapters. SDK providers require their corresponding optional dependency; configure an endpoint/model supported by the selected adapter.

---

## Config

The WebUI uses VoxNexus. Its built-in defaults and sign-in flow are documented in [VoxNexus setup](voxnexus.md). The example below is for an advanced direct-provider CLI/library configuration; it is not the default WebUI onboarding.

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
embeddable core · a VoxNexus sign-in with available quota for the default WebUI. Advanced
CLI/library providers require their own API key or a running Ollama instance. The one-click installer adds WebSocket, calendar, and SQLCipher
runtime extras automatically.

---

## Personal memory upgrade

The latest upgrade makes personal memory **inspectable and correctable**, while preserving existing conversations and files.

| Upgrade | What changes for you |
|---|---|
| **Reply ratings and viewpoint excerpts** | Rate a reply’s helpfulness, or select a fragment to mark inspiration, endorsement or disagreement. Save references separately from endorsed viewpoints/methods; edit conditions, revise or retract later. |
| **Explicit memory admission** | Assistant text is never automatically treated as your own stance. Saved references remain unendorsed; personal learning requires matching user quotations. Relevant explicit feedback joins existing context retrieval without another model call. |
| **Per-answer understanding** | A compact **本次理解** entry shows your question, explicitly expressed viewpoints, historical references, and matching answer excerpts. Counts describe evidence—not a fabricated “understands you” percentage. |
| **Correctable personal context** | Confirm a useful record or stop referencing an inaccurate one through the shared confirmation dialog. Original conversations remain intact. |
| **Continuous context and evolving viewpoints** | Short follow-ups borrow the preceding question for retrieval; opinion updates can reuse an existing topic and retain refinement/reversal history. Current instructions take precedence over older inferences. |
| **More reliable recall** | Rank-based keyword/vector fusion, Chinese-aware tokenization, restart-stable local vectors, bounded query caching, and a repair command for old or mismatched indexes. |
| **Recoverable background learning** | Understanding review and per-turn learning use database-backed jobs with bounded retries and restart recovery, without holding up the completed answer. |
| **Topic-named Markdown downloads** | Downloads use a document title, then the session name, then a short question-derived name. Names are sanitized and length-limited; no extra model request is needed. |

These build on the existing compact chat UI: a single line of live runtime status, an inline streaming cursor, file ratings/tags, and shared confirmation dialogs. See [Browser UI](#browser-ui) and [Upgrade Notes](#upgrade-notes) for details.
