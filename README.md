<div align="center">

<img src="hushclaw/web/icon.svg" alt="HushClaw" width="88" height="88">

# HushClaw

### A personal AI assistant that gets to know you.

**Local first. Your memory and data stay yours.**

HushClaw remembers your preferences, projects and way of working,<br>
learns from your feedback and corrections, and runs on your own computer.

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square)
![Local First](https://img.shields.io/badge/Local-First-2563EB?style=flat-square)
![Personal Memory](https://img.shields.io/badge/Memory-Yours-0F766E?style=flat-square)

[Quick start](#quick-start) · [Memory](#memory) · [Self-improvement](#learning) · [Privacy](#local-first) · [Docs](#docs)

</div>

---

| Knows you | Keeps getting better | Stays on your machine |
| :--- | :--- | :--- |
| Your preferences, background, projects and methods carry over across sessions and models. | It reflects on every turn, learns from your corrections and ratings, and refines its own skills. | Memory, conversations and files are stored, encrypted and backed up locally. |
| **Understanding builds up.** | **Mistakes turn into lessons.** | **Your data belongs to you.** |

Models change, products change, and your context ends up scattered across all of them. HushClaw gives that context one long-lived home: a single assistant that keeps a personal record of what you care about and how you like to work, uses it on every answer, and corrects it when you tell it something is wrong. You can switch models at any time; what HushClaw has learned about you stays.

<a id="memory"></a>

## 01 · An assistant that knows you deeply

Before each answer, HushClaw recalls the relevant parts of your personal record and hands them to the current model, within an explicit context budget.

| What it remembers | How it helps next time |
| :--- | :--- |
| **Your profile** | Communication style, workflow habits, recurring goals (`USER.md`). |
| **Your projects** | Facts, materials, goals and decisions you have already made. |
| **Your viewpoints** | How your opinions evolve, with their corrections and the conditions they apply to. |
| **Your experience** | Lessons distilled from task results and corrections, ready to be recalled. |

For example, tell it once that "reports should lead with the conclusion and list the risks". Saved as a preference, that rule is recalled even after you switch to a different model.

Every answer can show what it relied on. The **result card** under a reply lists the memories, skills and files that turn used or created, and you can confirm, correct or undo them on the spot. Memory is **kept long term, recalled on demand, and corrected by you**: recall is still gated by relevance and the context budget, and different models will use the same context differently.

<a id="learning"></a>

## 02 · Reflects and improves itself

HushClaw is built as a reflective harness. Each turn produces a trace that the learning loop reviews afterwards:

- **Reflection after every turn.** Tool traces are scored (clean, errors, or corrected) and the lessons are stored per task type.
- **Corrections count.** When you correct an answer, the correction is attributed to the turn it fixes, lowers the score of the skill involved, and can trigger an automatic skill refinement.
- **Ratings and disputes.** Star ratings and disputed passages feed the same signals.
- **Personal regression set.** Corrections and ratings become personal eval cases. `hushclaw eval run` replays them with read-only tools so you can check that the assistant has not regressed.
- **Skills that evolve.** Reusable ways of working live in `SKILL.md` files; skills with strong signals are patched automatically, and new skills only affect new sessions so active conversations stay stable.

<a id="local-first"></a>

## 03 · Local first, private by design

HushClaw runs on your computer and you use it in the browser. Conversations, memory, tasks, configuration and work files are managed locally and can be backed up and moved.

- **Runs locally.** Single-user by default, and the server listens on the loopback address only. Requests from foreign browser origins and DNS-rebinding hosts are rejected.
- **Held locally.** Your memory is stored independently of any model account or provider.
- **Protected locally.** The installer enables SQLCipher database encryption by default, with the key kept in the system credential store where available.
- **Sensitive tools need approval.** Shell commands and skill installs ask for confirmation in the WebUI. Chat connectors and inbound automation cannot run them unless you opt in, and third-party content is wrapped as untrusted before it reaches the model.

When you use a cloud model, the messages, file excerpts and recalled memories needed for a task are sent to the model service you configured, and web tools and enabled connectors reach external services. "Local first" describes who holds and manages the data.

<a id="models"></a>

## 04 · Pick the right model for the task

**VoxNexus is the default unified model gateway.** After signing in, the Settings page lets you browse available models, switch the main model, set a background model, check your balance and top up.

| Use case | How to choose |
| :--- | :--- |
| Everyday chat and hard problems | Pick a main model and switch whenever you like. |
| Memory extraction and background work | Use a separate background model to balance quality and cost. |
| Specialised tasks and multi-agent work | Give each agent its own model and tools, then route tasks to one agent, broadcast, or run a pipeline. |

The model list comes live from the gateway, which also handles upstream routing, so coverage, pricing and availability follow the current list. Switching models never replaces your local memory.

<details>
<summary><strong>Developers: connect models directly or add a provider</strong></summary>

Adapters for Anthropic, OpenAI and OpenAI-compatible APIs, Google Gemini and Ollama remain available for the CLI, library use and custom deployments. Install the matching SDK extras as needed.

The default WebUI uses VoxNexus. See the [technical reference](docs/technical-reference.md#config) for configuring other providers.

</details>

<a id="quick-start"></a>

## Quick start

**macOS / Linux**

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/CNTWDev/hushclaw/master/install.sh)
```

**Windows · PowerShell**

```powershell
irm https://raw.githubusercontent.com/CNTWDev/hushclaw/master/install.ps1 | iex
```

The installer prepares a Python environment, installs runtime dependencies, initialises the local database and starts the service. On macOS without Python 3.11+, it checks for Homebrew, installs it if needed and then installs Python; follow the terminal prompts for admin approval the first time. An existing usable Python is reused. Day-to-day use needs neither an npm build nor Docker.

Progress is shown by stage, and dependency install logs are kept in `~/.hushclaw/logs/`. If another program holds the port, the installer will not stop it, and a failed upgrade tries to restore the previously running service. See [installation and recovery](docs/installer.md).

1. Open your [local workspace](http://localhost:8765/personal).
2. Go to **Settings → Account** and sign in to VoxNexus in your system browser.
3. Check your balance, top up if needed, then choose a main model and a background model.
4. Start chatting and add your materials; your personal context grows as you use it.

The default gateway address and the `hushclaw-desktop` client are built in. Sign-in tokens are stored in the system keychain, and the browser and the HushClaw service must run on the same computer. See [VoxNexus setup](docs/voxnexus.md) for deployment details.

## One workspace for daily work

| Area | What you can do |
| :--- | :--- |
| **Chat** | Streaming conversation, document analysis and file handling, with one short status line while it works. |
| **Memories** | Browse preferences, notes, viewpoints and lessons, check their sources and fix them. |
| **Files** | Manage imported and generated files: search, tag, rate, preview and download. |
| **Agents & Skills** | Set up assistants, tools and skills for different kinds of work and have them collaborate. |
| **Tasks & Calendar** | Manage tasks and your schedule, optionally connected to a local or external calendar. |
| **Connections** | Connect email, the browser and other external services when you need them. |

Developer mode adds tool input and result previews to the Runtime panel, kept separate from the reply. External connectors need their own authorisation and depend on your OS, configuration and installed extras.

## Take your data with you

Local data directory:

| OS | Location |
| :--- | :--- |
| macOS | `~/Library/Application Support/hushclaw/` |
| Linux | `~/.local/share/hushclaw/` |
| Windows | `%LOCALAPPDATA%\hushclaw\` |

```bash
hushclaw backup export                      # export a local backup
hushclaw backup import /path/to/backup.zip  # restore on the target machine
hushclaw database status                    # check database encryption and integrity
```

Backups of an encrypted database stay encrypted, so keep the recovery key somewhere safe. Database encryption does not cover every work file or exported document; see [local data security](docs/technical-reference.md#local-data-security) for the full scope and migration steps.

<details>
<summary><strong>Development and extension</strong></summary>

Requires Python 3.11+. Install from source:

```bash
git clone https://github.com/CNTWDev/hushclaw.git
cd hushclaw
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[server,encryption]"
hushclaw serve
```

The core has zero mandatory third-party dependencies and can be embedded on its own; the server, model SDKs, browser and connectors are optional extras. The WebUI ships as prebuilt static assets.

- **Tools**: extend capabilities with the Python `@tool` decorator.
- **Skills**: package reusable ways of working as `SKILL.md`.
- **Runtime**: the agent kernel, the personal distribution and the WebUI / CLI are separate layers that share one context, tool and memory pipeline.

Run the tests with `python -m pytest tests/`. See the [architecture and configuration reference](docs/technical-reference.md) for multi-agent setup, providers, tool discovery and developer commands.

</details>

<a id="docs"></a>

## Learn more

| Resource | Contents |
| :--- | :--- |
| [hushclaw-web](https://github.com/CNTWDev/hushclaw-web) | The official website and community skill directory, with its own one-line server install. |
| [VoxNexus setup](docs/voxnexus.md) | Sign-in, model selection, balance, top-ups and deployment. |
| [Technical reference](docs/technical-reference.md) | Runtime architecture, memory and learning systems, advanced config, upgrades and migration. |
| [Memory architecture](docs/memory-evolution-architecture.md) | Technical design of personal context and memory evolution. |
| [Architecture decisions](docs/adr/) | ADRs for the kernel, tool surface, storage security and more. |
| [Google Calendar](docs/google-calendar.md) | Setting up the optional calendar connection. |
| [UI design system](docs/ui-design-system.md) | Interface structure and interaction conventions. |
| [Changelog](CHANGELOG.md) | Release notes. |

---

<div align="center">

**HushClaw · A personal AI assistant that gets to know you.**<br>
Local first. Your memory and data stay yours.

</div>
