"""Low-level application paths shared by config and secret storage."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def get_config_dir() -> Path:
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


def get_data_dir() -> Path:
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
