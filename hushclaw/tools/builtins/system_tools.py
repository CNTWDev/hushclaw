"""System information tools."""
from __future__ import annotations

import platform
import sys
import time

from hushclaw.tools.base import tool, ToolResult


@tool(
    name="get_time",
    description="Get the current date and time.",
)
def get_time() -> ToolResult:
    """Return current ISO timestamp."""
    return ToolResult.ok(time.strftime("%Y-%m-%dT%H:%M:%S%z"))


@tool(
    name="platform_info",
    description="Get information about the current operating system and Python version.",
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
