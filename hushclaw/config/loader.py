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
        email=make(EmailConfig, raw.get("email", {})),
        calendar=make(CalendarConfig, raw.get("calendar", {})),
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

    # Resolve user_skill_dir — optional, no default
    if config.tools.user_skill_dir is not None:
        config.tools.user_skill_dir = Path(config.tools.user_skill_dir).expanduser()

    # Resolve workspace_dir — priority:
    #   1. Explicitly set in config
    #   2. .hushclaw/ in cwd (project-local override)
    #   3. ~/.hushclaw/workspace (default global workspace, auto-created)
    if config.agent.workspace_dir is not None:
        config.agent.workspace_dir = Path(config.agent.workspace_dir).expanduser()

    # Resolve trajectory_dir — optional, no default
    if config.agent.trajectory_dir is not None:
        config.agent.trajectory_dir = Path(config.agent.trajectory_dir).expanduser()
    else:
        # Priority 2: project-local
        auto_ws = Path.cwd() / ".hushclaw"
        if auto_ws.is_dir():
            config.agent.workspace_dir = auto_ws
        else:
            # Priority 3: global default workspace — always available
            default_ws = _data_dir() / "workspace"
            config.agent.workspace_dir = default_ws

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


def _sync_api_keys_to_env(api_keys: dict) -> None:
    """One-way sync: config api_keys → os.environ for skill tools.

    Rules:
    - Config has value  + env not set  → set env var
    - Config has value  + env already set → env wins, leave it
    - Config has empty/missing          → if WE set it before, clear it
    """
    if not isinstance(api_keys, dict):
        return
    for cfg_key, env_var in _API_KEY_ENV_MAP.items():
        value = api_keys.get(cfg_key, "")
        if not isinstance(value, str):
            value = ""
        value = value.strip()
        existing = os.environ.get(env_var, "")
        if value and not existing:
            # Config has key, env var not yet set → promote
            os.environ[env_var] = value
        elif not value and existing:
            # Config cleared the key; only remove if it looks like we set it
            # (i.e., it matches what we'd have set — avoids nuking user env vars
            # that happen to have the same name but different values)
            pass  # conservative: never delete; user can unset manually if needed


_DEFAULT_SOUL_MD = """\
# Agent Identity

You are HushClaw, an intelligent personal assistant with persistent memory.

## Memory-First Behavior

At the start of every conversation or task:
1. Call `recall()` with relevant keywords to check prior context about the topic,
   project, or user preference.
2. If memories are found, reference them explicitly — do not start from scratch.
3. Ask clarifying questions only after checking memory first.

After completing important tasks:
- Call `remember()` to save: outcomes, file paths, key decisions, user preferences.
- Use descriptive titles (e.g. "PPT: Russia AI market 2026 — saved to ~/Desktop/...")
  so memories can be retrieved in future sessions.
- Do not narrate memory operations in normal replies (avoid phrases like
  "saved to memory") unless the user explicitly asks for audit details.

## Work Style

- Be direct and decisive — skip filler phrases like "Great question!"
- Prefer action over clarification when context is sufficient
- Cite specific recalled memories when continuing prior work
- Summarize what you remembered at the start of each task
"""

_DEFAULT_USER_MD = """\
# User Notes

*Edit this file directly to add persistent preferences visible to the agent.*

## Preferences
<!-- Add user preferences here, e.g. language, tone, output format -->

## Active Projects
<!-- Add current projects with context, e.g.:
- Project: Russia AI Music Market Report — PPT at ~/Desktop/russia_ai_music.pptx
- Project: Africa Health App PRD — draft at ~/Desktop/africa_health_app_prd.md
-->

## Key Decisions
<!-- Important decisions and their rationale -->
"""

_DEFAULT_AGENTS_MD = """\
# Agent Behavior Rules

*Edit this file to change how the agent behaves. It overrides built-in defaults.*

## Memory-First Behavior
At the start of every task or conversation, proactively call recall() with
relevant keywords to check if you have prior context about this topic, project,
or user preference. Reference recalled memories explicitly — never start from
scratch when history exists.

After completing any important task (generating a document, making a key decision,
finishing a research task), call remember() to save: the outcome, the file path,
key decisions made, and any user preferences expressed. Use a descriptive title
so the memory can be retrieved later.

## Skill-First Behavior
Before starting any complex or multi-step task, call list_skills to check if a
relevant skill exists. If one matches, call use_skill to load its instructions
and follow them exactly. After successfully completing a task using a non-obvious
approach, call remember_skill to save it as a reusable skill for future use.

## Web Access Rules
1. For social media platforms (TikTok, Twitter/X, Instagram, LinkedIn, YouTube,
   Weibo, Xiaohongshu/RED, WeChat, Facebook, etc.) and any site requiring login
   or JavaScript rendering, use browser_navigate + browser_get_content.
   NEVER use fetch_url for these.
2. If you receive a login-wall response, call browser_open_for_user to let the
   user log in, then browser_wait_for_user.
3. Use fetch_url only for plain public APIs, RSS feeds, or raw data endpoints.
4. For downloadable files produced by tools, only return relative links starting
   with '/files/'. Use public_base_url for absolute links if explicitly needed.

## Work Style
- Be direct and decisive — skip filler phrases like "Great question!"
- Prefer action over clarification when context is sufficient
- Cite specific recalled memories when continuing prior work
"""


def _bootstrap_workspace(ws_dir: Path) -> None:
    """Create workspace directory and seed default files if they don't exist."""
    try:
        ws_dir.mkdir(parents=True, exist_ok=True)
        soul = ws_dir / "SOUL.md"
        if not soul.exists():
            soul.write_text(_DEFAULT_SOUL_MD, encoding="utf-8")
        user = ws_dir / "USER.md"
        if not user.exists():
            user.write_text(_DEFAULT_USER_MD, encoding="utf-8")
        agents = ws_dir / "AGENTS.md"
        if not agents.exists():
            agents.write_text(_DEFAULT_AGENTS_MD, encoding="utf-8")
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
