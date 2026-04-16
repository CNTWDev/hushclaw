"""System information tools."""
from __future__ import annotations

import platform
import sys
import time

from hushclaw.tools.base import tool, ToolResult


@tool(
    name="get_time",
    description="Get the current date and time.",
    parallel_safe=True,
)
def get_time() -> ToolResult:
    """Return current ISO timestamp."""
    return ToolResult.ok(time.strftime("%Y-%m-%dT%H:%M:%S%z"))


@tool(
    name="platform_info",
    description="Get information about the current operating system and Python version.",
    parallel_safe=True,
)
def platform_info() -> ToolResult:
    """Return OS and runtime information."""
    info = {
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    lines = [f"{k}: {v}" for k, v in info.items()]
    return ToolResult.ok("\n".join(lines))


# Config key → environment variable name (mirrors loader._API_KEY_ENV_MAP)
_API_KEY_CFG_TO_ENV: dict[str, str] = {
    "scrape_creators":      "SCRAPE_CREATORS_API_KEY",
    "tiktok_client_key":    "TIKTOK_CLIENT_KEY",
    "tiktok_client_secret": "TIKTOK_CLIENT_SECRET",
}
_API_KEY_ENV_TO_CFG: dict[str, str] = {v: k for k, v in _API_KEY_CFG_TO_ENV.items()}


@tool(
    name="set_api_key",
    description=(
        "Set a skill API key. Saves it to the config file for persistence and activates it "
        "immediately in the current session without a restart. "
        "Accepted key_name values: scrape_creators, tiktok_client_key, tiktok_client_secret "
        "(or the corresponding env var names: SCRAPE_CREATORS_API_KEY, TIKTOK_CLIENT_KEY, "
        "TIKTOK_CLIENT_SECRET). Pass an empty string for value to clear the key."
    ),
)
def set_api_key(key_name: str, value: str) -> ToolResult:
    """Persist a skill API key to hushclaw.toml and inject it into os.environ immediately."""
    import os

    key = key_name.strip()
    # Accept both config-key style ("scrape_creators") and env-var style ("SCRAPE_CREATORS_API_KEY")
    cfg_key = _API_KEY_ENV_TO_CFG.get(key) or key.lower().replace("-", "_")
    env_var = _API_KEY_CFG_TO_ENV.get(cfg_key, key.upper())
    value = value.strip()

    # 1. Set / clear in the current process immediately so skill tools see it right away.
    if value:
        os.environ[env_var] = value
    else:
        os.environ.pop(env_var, None)

    # 2. Persist to config file (best-effort — active-session result stands even if this fails).
    saved = False
    save_err = ""
    try:
        from hushclaw.config.loader import get_config_dir
        from hushclaw.config.writer import set_config_value
        cfg_file = get_config_dir() / "hushclaw.toml"
        set_config_value(cfg_file, f"api_keys.{cfg_key}", value)
        saved = True
    except Exception as exc:
        save_err = str(exc)

    masked = (value[:4] + "…" + "*" * max(0, len(value) - 4)) if len(value) > 4 else "****"
    if not value:
        status = f"Cleared {env_var}"
        suffix = " (saved to config)." if saved else f" (config save failed: {save_err})."
    else:
        status = f"Set {env_var} = {masked}"
        suffix = " — active now and saved to config (persists across restarts)." if saved \
            else f" for this session only (config save failed: {save_err})."
    return ToolResult.ok(status + suffix)


@tool(
    name="list_api_keys",
    description=(
        "List all configured skill API keys and their status (set / not set). "
        "Values are masked — only shows whether each key is present and a hint of the value. "
        "Use this to check what keys are configured before asking the user to provide one."
    ),
)
def list_api_keys() -> ToolResult:
    """Read api_keys section from config and report status of each known key."""
    try:
        import tomllib
        from hushclaw.config.loader import get_config_dir
        cfg_file = get_config_dir() / "hushclaw.toml"
        data: dict = {}
        if cfg_file.exists():
            with open(cfg_file, "rb") as f:
                data = tomllib.load(f)
    except Exception as exc:
        return ToolResult.error(f"Could not read config: {exc}")

    api_keys: dict = data.get("api_keys", {})
    # Show known keys first, then any extra keys stored in config
    all_keys = list(dict.fromkeys(list(_API_KEY_CFG_TO_ENV.keys()) + list(api_keys.keys())))

    if not all_keys:
        return ToolResult.ok("No API keys configured. Use set_api_key to add one.")

    lines = []
    for k in all_keys:
        env_var = _API_KEY_CFG_TO_ENV.get(k, k.upper())
        val = api_keys.get(k, "")
        if val and isinstance(val, str) and val.strip():
            v = val.strip()
            masked = v[:4] + "…" + "*" * max(0, len(v) - 4)
            lines.append(f"● {k}  ({env_var})  [{masked}]")
        else:
            lines.append(f"○ {k}  ({env_var})  [not set]")
    return ToolResult.ok("\n".join(lines))

