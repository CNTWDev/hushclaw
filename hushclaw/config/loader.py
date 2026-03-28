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
    EmailConfig, CalendarConfig,
)
from hushclaw.exceptions import ConfigError


def _config_dir() -> Path:
    """Return platform-specific config directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "hushclaw"
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    return (Path(xdg) if xdg else Path.home() / ".config") / "hushclaw"


def _data_dir() -> Path:
    """Return platform-specific data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "hushclaw"
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
        "HUSHCLAW_DATA_DIR": ("memory", "data_dir"),
        "HUSHCLAW_LOG_LEVEL": ("logging", "level"),
        # Provider-specific API keys
        "ANTHROPIC_API_KEY": ("provider", "api_key"),
        "OPENAI_API_KEY": ("provider", "api_key"),
        "AIGOCODE_API_KEY": ("provider", "api_key"),
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
    _provider_specific = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AIGOCODE_API_KEY"}
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

    # Resolve workspace_dir — auto-detect .hushclaw/ in cwd if not set explicitly
    if config.agent.workspace_dir is None:
        auto_ws = Path.cwd() / ".hushclaw"
        if auto_ws.is_dir():
            config.agent.workspace_dir = auto_ws
    else:
        config.agent.workspace_dir = Path(config.agent.workspace_dir).expanduser()

    return config


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
        warnings.append(
            f"[ERROR] provider.api_key is not set for provider '{config.provider.name}'. "
            "Set ANTHROPIC_API_KEY (or OPENAI_API_KEY) or configure in hushclaw.toml."
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
