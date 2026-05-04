"""Config loading: defaults → user config → project config → env vars."""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from dataclasses import fields

from hushclaw.config.schema import (
    Config, AgentConfig, ProviderConfig, MemoryConfig, ToolsConfig, LoggingConfig,
    ContextPolicyConfig, AgentDefinition, GatewayConfig, ServerConfig, UpdateConfig,
    TelegramConfig, FeishuConfig, DiscordConfig, SlackConfig,
    DingTalkConfig, WeChatWorkConfig, ConnectorsConfig, BrowserConfig,
    EmailConfig, CalendarConfig, TranssionConfig, WorkspaceEntry, WorkspacesConfig,
)
from hushclaw.exceptions import ConfigError


def _config_dir() -> Path:
    """Return platform-specific config directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "hushclaw"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "hushclaw"
        return Path.home() / "AppData" / "Roaming" / "hushclaw"
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    return (Path(xdg) if xdg else Path.home() / ".config") / "hushclaw"


def _data_dir() -> Path:
    """Return platform-specific data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "hushclaw"
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            return Path(local_appdata) / "hushclaw"
        return Path.home() / "AppData" / "Local" / "hushclaw"
    xdg = os.environ.get("XDG_DATA_HOME", "")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "hushclaw"


# Public aliases used by cli.py and writer.py
get_config_dir = _config_dir
get_data_dir = _data_dir


def _load_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {path}: {e}") from e


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _apply_env(raw: dict) -> dict:
    """Apply HUSHCLAW_* environment variables."""
    mapping = {
        "HUSHCLAW_MODEL": ("agent", "model"),
        "HUSHCLAW_MAX_TOKENS": ("agent", "max_tokens"),
        "HUSHCLAW_PROVIDER": ("provider", "name"),
        "HUSHCLAW_API_KEY": ("provider", "api_key"),
        "HUSHCLAW_BASE_URL": ("provider", "base_url"),
        "HUSHCLAW_PUBLIC_BASE_URL": ("server", "public_base_url"),
        "HUSHCLAW_DATA_DIR": ("memory", "data_dir"),
        "HUSHCLAW_LOG_LEVEL": ("logging", "level"),
        # Provider-specific API keys
        "ANTHROPIC_API_KEY": ("provider", "api_key"),
        "OPENAI_API_KEY": ("provider", "api_key"),
        "AIGOCODE_API_KEY": ("provider", "api_key"),
        "GEMINI_API_KEY": ("provider", "api_key"),
        "MINIMAX_API_KEY": ("provider", "api_key"),
        "TRANSSION_API_KEY": ("provider", "api_key"),
        # Connector credentials — nested path as tuple
        "TELEGRAM_BOT_TOKEN":   ("connectors", "telegram", "bot_token"),
        "FEISHU_APP_ID":        ("connectors", "feishu", "app_id"),
        "FEISHU_APP_SECRET":    ("connectors", "feishu", "app_secret"),
        "DISCORD_BOT_TOKEN":    ("connectors", "discord", "bot_token"),
        "SLACK_BOT_TOKEN":      ("connectors", "slack", "bot_token"),
        "SLACK_APP_TOKEN":      ("connectors", "slack", "app_token"),
        "DINGTALK_CLIENT_ID":   ("connectors", "dingtalk", "client_id"),
        "DINGTALK_CLIENT_SECRET": ("connectors", "dingtalk", "client_secret"),
        "WECOM_CORP_ID":        ("connectors", "wecom", "corp_id"),
        "WECOM_CORP_SECRET":    ("connectors", "wecom", "corp_secret"),
        "HUSHCLAW_EMAIL_PASSWORD":    ("email", "password"),
        "HUSHCLAW_CALENDAR_PASSWORD": ("calendar", "password"),
    }
    raw = {k: dict(v) if isinstance(v, dict) else v for k, v in raw.items()}
    for k, v in raw.items():
        if isinstance(v, dict):
            raw[k] = dict(v)
        # Normalize email/calendar single-account dicts to lists early so env-var
        # injection below can safely set raw["email"][0]["password"].
        elif isinstance(v, list):
            pass  # already a list (array-of-tables from TOML)

    provider_name = raw.get("provider", {}).get("name", "anthropic-raw")
    # Provider-specific env keys (ANTHROPIC_API_KEY etc.) are convenience shortcuts
    # for users who have NOT configured an explicit api_key in their TOML.  When the
    # user has saved a key through the wizard (or hand-edited the TOML), that value
    # takes priority — otherwise a system-wide OPENAI_API_KEY would silently override
    # an OpenRouter/compatible key and cause spurious 401 errors.
    toml_api_key = raw.get("provider", {}).get("api_key", "")
    _provider_specific = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AIGOCODE_API_KEY", "GEMINI_API_KEY", "MINIMAX_API_KEY", "TRANSSION_API_KEY"}
    for env_key, path in mapping.items():
        val = os.environ.get(env_key)
        if val is None:
            continue
        if env_key == "ANTHROPIC_API_KEY" and "anthropic" not in provider_name:
            continue
        if env_key == "OPENAI_API_KEY" and "openai" not in provider_name:
            continue
        if env_key == "AIGOCODE_API_KEY" and "aigocode" not in provider_name:
            continue
        if env_key == "GEMINI_API_KEY" and provider_name not in ("gemini", "google"):
            continue
        if env_key == "MINIMAX_API_KEY" and "minimax" not in provider_name:
            continue
        # Don't let a provider-specific env var clobber an explicitly configured key
        field = path[-1]
        if env_key in _provider_specific and field == "api_key" and toml_api_key:
            continue
        # Navigate/create nested dicts for multi-level paths
        # email and calendar are now lists — inject into first element only
        if path[0] in ("email", "calendar") and len(path) == 2:
            lst = raw.setdefault(path[0], [{}])
            if not lst:
                lst.append({})
            if isinstance(lst[0], dict):
                lst[0][path[1]] = val
            continue
        node = raw
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[field] = val

    return raw


def _make_workspaces_config(data: dict) -> WorkspacesConfig:
    entries = []
    for w in data.get("list", []):
        if isinstance(w, dict) and w.get("name") and w.get("path"):
            entries.append(WorkspaceEntry(
                name=str(w["name"]),
                path=str(w["path"]),
                description=str(w.get("description", "")),
            ))
    return WorkspacesConfig(list=entries)


def _make_simple(cls, data: dict):
    """Instantiate a simple dataclass from a dict, using field defaults for missing keys."""
    from dataclasses import fields as _fields, MISSING
    kwargs = {}
    for f in _fields(cls):
        if f.name in data:
            kwargs[f.name] = data[f.name]
        elif f.default is not MISSING:
            kwargs[f.name] = f.default
        elif f.default_factory is not MISSING:
            kwargs[f.name] = f.default_factory()
    return cls(**kwargs)


def _parse_account_list(cls, raw_val) -> list:
    """Parse email or calendar config: list of dicts, or single dict (backward compat)."""
    if isinstance(raw_val, list):
        return [_make_simple(cls, item) for item in raw_val if isinstance(item, dict)]
    if isinstance(raw_val, dict) and raw_val:
        return [_make_simple(cls, raw_val)]
    return []


def _make_gateway_config(data: dict) -> GatewayConfig:
    agents = []
    for a in data.get("agents", []):
        valid = {f for f in AgentDefinition.__dataclass_fields__}
        agents.append(AgentDefinition(**{k: v for k, v in a.items() if k in valid}))
    pipelines = data.get("pipelines", {})
    if not isinstance(pipelines, dict):
        pipelines = {}
    return GatewayConfig(
        agents=agents,
        shared_memory=data.get("shared_memory", True),
        max_concurrent_per_agent=data.get("max_concurrent_per_agent", 10),
        pipelines=pipelines,
        session_ttl_hours=data.get("session_ttl_hours", 24),
        scheduled_session_mode=data.get("scheduled_session_mode", "job"),
        session_list_limit=data.get("session_list_limit", 200),
        session_list_idle_days=data.get("session_list_idle_days", 0),
        session_list_hide_scheduled=data.get("session_list_hide_scheduled", False),
    )


def _dict_to_config(raw: dict) -> Config:
    def make(cls, data):
        kwargs = {}
        for f in fields(cls):
            val = data.get(f.name, f.default if f.default is not f.default_factory else None)
            if f.name == "data_dir" and val is not None:
                val = Path(val) if val else None
            elif f.name == "plugin_dir" and val is not None:
                val = Path(val) if val else None
            elif f.name == "skill_dir" and val is not None:
                val = Path(val) if val else None
            elif f.name == "user_skill_dir" and val is not None:
                # Empty string means "not configured" — treat same as None
                val = Path(val) if val else None
            elif f.name == "workspace_dir" and val is not None:
                val = Path(val) if val else None
            elif f.name == "trajectory_dir" and val is not None:
                val = Path(val) if val else None
            elif f.name == "upload_dir" and val is not None:
                val = Path(val) if val else None
            elif f.name == "enabled" and isinstance(val, list):
                pass
            elif f.name not in data:
                continue
            kwargs[f.name] = val
        return cls(**kwargs)

    conn_raw = raw.get("connectors", {})
    connectors = ConnectorsConfig(
        telegram=make(TelegramConfig,   conn_raw.get("telegram", {})),
        feishu=make(FeishuConfig,       conn_raw.get("feishu", {})),
        discord=make(DiscordConfig,     conn_raw.get("discord", {})),
        slack=make(SlackConfig,         conn_raw.get("slack", {})),
        dingtalk=make(DingTalkConfig,   conn_raw.get("dingtalk", {})),
        wecom=make(WeChatWorkConfig,    conn_raw.get("wecom", {})),
    )

    # api_keys is a free-form dict; loaded as-is from TOML
    raw_api_keys = raw.get("api_keys", {})
    if not isinstance(raw_api_keys, dict):
        raw_api_keys = {}

    return Config(
        agent=make(AgentConfig, raw.get("agent", {})),
        provider=make(ProviderConfig, raw.get("provider", {})),
        memory=make(MemoryConfig, raw.get("memory", {})),
        tools=make(ToolsConfig, raw.get("tools", {})),
        logging=make(LoggingConfig, raw.get("logging", {})),
        context=make(ContextPolicyConfig, raw.get("context", {})),
        gateway=_make_gateway_config(raw.get("gateway", {})),
        server=make(ServerConfig, raw.get("server", {})),
        update=make(UpdateConfig, raw.get("update", {})),
        connectors=connectors,
        browser=make(BrowserConfig, raw.get("browser", {})),
        emails=_parse_account_list(EmailConfig, raw.get("email", [])),
        calendars=_parse_account_list(CalendarConfig, raw.get("calendar", [])),
        transsion=make(TranssionConfig, raw.get("transsion", {})),
        workspaces=_make_workspaces_config(raw.get("workspaces", {})),
        api_keys=raw_api_keys,
    )


def load_config(project_dir: Path | None = None) -> Config:
    """Load configuration from all sources, merging in priority order."""
    cfg_dir = _config_dir()
    user_cfg = _load_toml(cfg_dir / "hushclaw.toml")

    # Project-level config
    search_dir = project_dir or Path.cwd()
    project_cfg: dict = {}
    for candidate in [search_dir / ".hushclaw.toml", search_dir / "hushclaw.toml"]:
        if candidate.exists():
            project_cfg = _load_toml(candidate)
            break

    raw = _deep_merge(user_cfg, project_cfg)
    raw = _apply_env(raw)

    # Migrate old default max_tool_rounds (10) → 30
    agent_raw = raw.get("agent", {})
    if agent_raw.get("max_tool_rounds") == 10:
        agent_raw["max_tool_rounds"] = 30
        raw["agent"] = agent_raw

    config = _dict_to_config(raw)

    # Resolve data_dir
    if config.memory.data_dir is None:
        env_dir = os.environ.get("HUSHCLAW_DATA_DIR")
        config.memory.data_dir = Path(env_dir) if env_dir else _data_dir()

    # Resolve upload_dir
    if config.server.upload_dir is None:
        config.server.upload_dir = config.memory.data_dir / "uploads"
    else:
        config.server.upload_dir = Path(config.server.upload_dir).expanduser()

    # Resolve plugin_dir
    if config.tools.plugin_dir is None:
        config.tools.plugin_dir = _config_dir() / "tools"

    # Resolve skill_dir — default to <data_dir>/skills so the Skills page
    # works without manual config. SkillRegistry only initialises if the
    # directory actually exists, so no empty dir is created automatically.
    if config.tools.skill_dir is None:
        config.tools.skill_dir = _data_dir() / "skills"
    else:
        config.tools.skill_dir = Path(config.tools.skill_dir).expanduser()

    # Resolve user_skill_dir — defaults to <data_dir>/user-skills/ so user-installed
    # skills are always separate from the system skill_dir. Created on startup so
    # directory listings always succeed even before any skill is installed.
    if config.tools.user_skill_dir is None:
        config.tools.user_skill_dir = _data_dir() / "user-skills"
    else:
        config.tools.user_skill_dir = Path(config.tools.user_skill_dir).expanduser()
    config.tools.user_skill_dir.mkdir(parents=True, exist_ok=True)

    # Resolve workspace_dir — priority:
    #   1. Explicitly set in config
    #   2. .hushclaw/ in cwd (project-local override)
    #   3. ~/.hushclaw/workspace (default global workspace, auto-created)
    if config.agent.workspace_dir is not None:
        config.agent.workspace_dir = Path(config.agent.workspace_dir).expanduser()
    else:
        auto_ws = Path.cwd() / ".hushclaw"
        if auto_ws.is_dir():
            config.agent.workspace_dir = auto_ws
        else:
            config.agent.workspace_dir = _data_dir() / "workspace"

    # Resolve trajectory_dir — optional, no default
    if config.agent.trajectory_dir is not None:
        config.agent.trajectory_dir = Path(config.agent.trajectory_dir).expanduser()

    # Resolve workspace registry paths
    for ws_entry in config.workspaces.list:
        ws_entry.path = str(Path(ws_entry.path).expanduser())

    # Bootstrap workspace: create directory + default SOUL.md/USER.md if missing
    _bootstrap_workspace(config.agent.workspace_dir)

    # Promote api_keys config values into env vars so skill tools can use
    # plain os.environ.get() without knowing about _config injection.
    # Env vars already set by the user are NOT overwritten (they take priority).
    _sync_api_keys_to_env(config.api_keys)

    return config


# Canonical mapping: config key → environment variable name
_API_KEY_ENV_MAP: dict[str, str] = {
    "scrape_creators":      "SCRAPE_CREATORS_API_KEY",
    "tiktok_client_key":    "TIKTOK_CLIENT_KEY",
    "tiktok_client_secret": "TIKTOK_CLIENT_SECRET",
}

# Track env vars that were set by this sync function so we can update them
# on subsequent config reloads.  User-set vars (not in this set) keep priority.
_ENV_VARS_WE_SET: set[str] = set()


def _sync_api_keys_to_env(api_keys: dict) -> None:
    """One-way sync: config api_keys → os.environ for skill tools.

    Rules:
    - Config has value  + env not set            → set env var, remember we set it
    - Config has value  + env set by US before   → update to new config value
    - Config has value  + env set externally     → env wins (user shell export)
    - Config cleared    + we set it before       → remove from env
    - Config cleared    + set externally         → leave it (don't touch user vars)
    """
    if not isinstance(api_keys, dict):
        return
    for cfg_key, env_var in _API_KEY_ENV_MAP.items():
        value = api_keys.get(cfg_key, "")
        if not isinstance(value, str):
            value = ""
        value = value.strip()
        existing = os.environ.get(env_var, "")
        if value:
            if not existing or env_var in _ENV_VARS_WE_SET:
                # Not yet set, or we set it previously (config update path)
                os.environ[env_var] = value
                _ENV_VARS_WE_SET.add(env_var)
            # else: set externally by the user's environment — leave it
        else:
            if env_var in _ENV_VARS_WE_SET:
                # Config cleared a key we previously set — remove it
                os.environ.pop(env_var, None)
                _ENV_VARS_WE_SET.discard(env_var)


_MEMORY_AFTER_TASKS = """\
After completing important tasks:
- Call `remember()` to save: outcomes, file paths, key decisions, user preferences.
- Use descriptive titles (e.g. "PPT: Russia AI market 2026 — saved to workspace/files/russia-ai-market-2026.md")
  so memories can be retrieved in future sessions.
- Only call `remember()` after you have already shown the result or answer to the user.
- Never make "saved to memory" the only visible outcome of a normal chat turn.
- Do not narrate memory operations in normal replies (avoid phrases like
  "saved to memory") unless the user explicitly asks for audit details.
"""

_OUTPUT_STYLE_SOUL = """\
## Output Style

- Lead with the conclusion or direct answer — put evidence after, not before.
- Every factual claim must be backed by a specific data point, log line, file path,
  or code reference.
- No trailing summaries ("In summary...", "As you can see...").
- No restating the question or task at the start of a reply.
- No filler acknowledgments ("Great question!", "Sure!", "Of course!").
- Be direct and decisive — skip filler phrases.
- Prefer action over clarification when context is sufficient.
"""

_LEGACY_DEFAULT_SOUL_MD = f"""\
# Agent Identity

You are HushClaw, an intelligent personal assistant with persistent memory.

## Memory-First Behavior

At the start of every conversation or task:
1. Call `recall()` with relevant keywords to check prior context about the topic,
   project, or user preference.
2. If memories are found, reference them explicitly — do not start from scratch.
3. Ask clarifying questions only after checking memory first.

{_MEMORY_AFTER_TASKS}

{_OUTPUT_STYLE_SOUL}
"""

_DEFAULT_SOUL_MD = f"""\
# Agent Identity

You are HushClaw, an intelligent personal assistant with persistent memory.

## Memory Behavior

Treat memory as a layered system:
1. Prefer the active session context and working state first.
2. Use auto-injected memories as background context when they are present.
3. Call `recall()` only for targeted follow-up searches when the user asks about
   prior decisions, preferences, or history that is not already clear from the
   current working state.
4. Do not force a memory lookup for short operational turns like "continue",
   "run tests", or "fix this".

{_MEMORY_AFTER_TASKS}

{_OUTPUT_STYLE_SOUL}
"""

_DEFAULT_USER_MD = """\
# User Profile

*HushClaw reads this every session. Fill in what's true, skip what isn't.*
*Structured profile facts are auto-extracted from conversation and shown in Memories → Profile.*

## Identity & Background
<!-- Your role, industry, and context.
  e.g. Senior backend engineer at a fintech startup; 8 years Python, 2 years Go; team of 5 -->

## Expertise & Blind Spots
<!-- What you know well, what you're still learning, what to skip explaining.
  e.g. Expert in distributed systems; learning React; don't explain git basics -->

## Core Technical Positions
<!-- Your strong opinions on tech, architecture, and design that should inform advice.
  e.g. Prefer SQLite over Postgres for small-to-mid projects; no ORM; tests before refactor -->

## Communication Style
<!-- How you prefer to receive information.
  e.g. Concise, evidence-first; no trailing summaries; code before explanation; Chinese for casual chat -->

## Active Goals & Projects
<!-- What you're actively working on — gives context for why you're asking things.
  e.g. Building an AI agent runtime; optimizing memory recall speed -->

## Tooling Preferences
<!-- Your preferred tools, frameworks, and libraries.
  e.g. pytest, poetry, Docker; TypeScript not JavaScript -->

## Avoidances
<!-- Things you explicitly don't want from the assistant.
  e.g. No disclaimers; no "of course!"; don't add docstrings to code I didn't touch -->
"""

_MEMORY_AFTER_TASKS_AGENTS = """\
After completing any important task (generating a document, making a key decision,
finishing a research task), call remember() to save: the outcome, the file path,
key decisions made, and any user preferences expressed. Use a descriptive title
so the memory can be retrieved later. Do this only after you have already given
the user the actual result, and never let remember() be the only visible outcome
of a normal chat turn. Prefer relative output paths so generated files land in
the workspace by default; only use Desktop or Downloads when the user explicitly
asks for that destination.
"""

_SKILL_FIRST_BEHAVIOR = """\
## Skill-First Behavior
Before starting any complex or multi-step task, call list_skills to check if a
relevant skill exists. If one matches, call use_skill to load its instructions
and follow them exactly. After successfully completing a task using a non-obvious
approach, call remember_skill to save it as a reusable skill for future use.
"""

_WEB_ACCESS_RULES = """\
## Web Access Rules
1. For social media platforms (TikTok, Twitter/X, Instagram, LinkedIn, YouTube,
   Weibo, Xiaohongshu/RED, WeChat, Facebook, etc.) and any site requiring login
   or JavaScript rendering, use browser_navigate + browser_get_content.
   NEVER use fetch_url for these.
2. If you receive a login-wall response, call browser_open_for_user to let the
   user log in, then browser_wait_for_user.
3. Use fetch_url only for plain public APIs, RSS feeds, or raw data endpoints.
4. For generated artifacts produced by tools, only return relative links starting
   with '/files/'. Never write directly to '/files/...'; write to a normal path
   first and register the result with make_download_url or make_download_bundle.
   Use public_base_url for absolute links if explicitly needed.
"""

_OUTPUT_STYLE_AGENTS = """\
## Output Style
- Lead with the conclusion or direct answer — put evidence after, not before.
- Every factual claim must be backed by a specific data point, log line, file path,
  or code reference. Vague statements without evidence are not acceptable.
- No trailing summaries ("In summary...", "As you can see...").
- No restating the question or task at the start of a reply.
- No filler acknowledgments ("Great question!", "Sure!", "Of course!").
- Use lists and code blocks to compress information; prefer structure over prose.
- Cite specific recalled memories when continuing prior work.
"""

_LEGACY_DEFAULT_AGENTS_MD = f"""\
# Agent Behavior Rules

*Edit this file to change how the agent behaves. It overrides built-in defaults.*

## Memory-First Behavior
At the start of every task or conversation, proactively call recall() with
relevant keywords to check if you have prior context about this topic, project,
or user preference. Reference recalled memories explicitly — never start from
scratch when history exists.

{_MEMORY_AFTER_TASKS_AGENTS}

{_SKILL_FIRST_BEHAVIOR}

{_WEB_ACCESS_RULES}

{_OUTPUT_STYLE_AGENTS}
"""

_DEFAULT_AGENTS_MD = f"""\
# Agent Behavior Rules

*Edit this file to change how the agent behaves. It overrides built-in defaults.*

## Memory Behavior
Use the active session context and working state as the primary source of
continuity. Treat recalled memory as supplemental context, not a mandatory first
step.

Call `recall()` only when it will materially help:
- the user asks about previous decisions, preferences, or prior work
- the task depends on historical context not already present in the current turn
- you need a targeted follow-up search beyond auto-injected memories

Do not call `recall()` by default for short operational turns such as
"continue", "fix this", "run tests", or other immediate execution requests.

{_MEMORY_AFTER_TASKS_AGENTS}

{_SKILL_FIRST_BEHAVIOR}

{_WEB_ACCESS_RULES}

{_OUTPUT_STYLE_AGENTS}
"""


def _write_default_or_migrate(path: Path, default_text: str, legacy_text: str = "") -> None:
    """Seed a workspace file, or migrate it when it still matches the old default."""
    if not path.exists():
        path.write_text(default_text, encoding="utf-8")
        return
    if not legacy_text:
        return
    try:
        if path.read_text(encoding="utf-8") == legacy_text:
            path.write_text(default_text, encoding="utf-8")
    except OSError:
        pass


def _bootstrap_workspace(ws_dir: Path) -> None:
    """Create workspace directory and seed default files if they don't exist."""
    try:
        ws_dir.mkdir(parents=True, exist_ok=True)
        _write_default_or_migrate(ws_dir / "SOUL.md", _DEFAULT_SOUL_MD, _LEGACY_DEFAULT_SOUL_MD)
        _write_default_or_migrate(ws_dir / "USER.md", _DEFAULT_USER_MD)
        _write_default_or_migrate(ws_dir / "AGENTS.md", _DEFAULT_AGENTS_MD, _LEGACY_DEFAULT_AGENTS_MD)
    except OSError:
        pass  # read-only fs or permission error — silently skip


def validate_config(config: "Config") -> list[str]:
    """Run sanity checks on a loaded Config.

    Returns a list of human-readable warning/error strings.
    Empty list means all clear.  Prefix conventions:
      [ERROR] — must fix before hushclaw will work correctly
      [WARN]  — may cause unexpected behaviour
      [INFO]  — informational only
    """
    import shutil as _shutil
    warnings: list[str] = []

    # Provider API key
    if "ollama" not in config.provider.name and not config.provider.api_key:
        n = config.provider.name.lower()
        if "gemini" in n or "google" in n:
            env_hint = "GEMINI_API_KEY"
        elif "minimax" in n:
            env_hint = "MINIMAX_API_KEY"
        elif "openai" in n:
            env_hint = "OPENAI_API_KEY"
        elif "aigocode" in n:
            env_hint = "AIGOCODE_API_KEY"
        else:
            env_hint = "ANTHROPIC_API_KEY"
        warnings.append(
            f"[ERROR] provider.api_key is not set for provider '{config.provider.name}'. "
            f"Set {env_hint} or configure provider.api_key in hushclaw.toml."
        )

    # compact_strategy validity
    valid_strategies = {"lossless", "summarize", "abstractive", "prune_tool_results"}
    if config.context.compact_strategy not in valid_strategies:
        warnings.append(
            f"[WARN] context.compact_strategy={config.context.compact_strategy!r} "
            f"is not one of {sorted(valid_strategies)}"
        )

    # tools.profile validity
    valid_profiles = {"", "full", "coding", "messaging", "minimal"}
    if config.tools.profile not in valid_profiles:
        warnings.append(
            f"[WARN] tools.profile={config.tools.profile!r} "
            f"is not one of {sorted(valid_profiles)}"
        )

    valid_sched_modes = {"job", "run"}
    if config.gateway.scheduled_session_mode not in valid_sched_modes:
        warnings.append(
            f"[WARN] gateway.scheduled_session_mode={config.gateway.scheduled_session_mode!r} "
            f"is not one of {sorted(valid_sched_modes)}"
        )

    if config.gateway.session_list_limit <= 0:
        warnings.append("[WARN] gateway.session_list_limit should be > 0")

    if config.update.channel not in {"stable", "prerelease"}:
        warnings.append(
            f"[WARN] update.channel={config.update.channel!r} is not one of ['stable', 'prerelease']"
        )

    # skill_dir existence
    if config.tools.skill_dir and not config.tools.skill_dir.exists():
        warnings.append(
            f"[INFO] tools.skill_dir={config.tools.skill_dir} does not exist yet "
            "(skills will be loaded from built-ins only)."
        )

    # workspace_dir consistency
    if config.agent.workspace_dir and not config.agent.workspace_dir.is_dir():
        warnings.append(
            f"[WARN] agent.workspace_dir={config.agent.workspace_dir} is set "
            "but does not exist as a directory."
        )

    # data_dir writable
    if config.memory.data_dir:
        try:
            config.memory.data_dir.mkdir(parents=True, exist_ok=True)
            test_file = config.memory.data_dir / ".doctor_write_test"
            test_file.touch()
            test_file.unlink()
        except OSError as e:
            warnings.append(
                f"[ERROR] Cannot write to data_dir {config.memory.data_dir}: {e}"
            )

    return warnings
