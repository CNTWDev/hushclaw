"""Shell execution tool.

NOTE: run_shell is NOT in the default tools.enabled list.
Enable it explicitly in config or via --tools flag:
    tools.enabled = ["remember", "recall", ..., "run_shell"]
"""
from __future__ import annotations

import asyncio
import os

from hushclaw.tools.base import tool, ToolResult

# Patterns that are always blocked regardless of user config.
# This is a lightweight deny-list for the most destructive commands.
_BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf ~/",
    "rm -r /",
    "> /dev/sda",
    "> /dev/hda",
    "mkfs",
    "dd if=",
    ":(){ :|:& };:",  # fork bomb
    "shutdown",
    "reboot",
    "halt",
]


@tool(
    name="run_shell",
    description=(
        "Run a shell command and return stdout/stderr. "
        "Use for tasks like running tests, git operations, checking build output, "
        "listing files, and other system operations. "
        "The command runs in the current working directory."
    ),
    timeout=120,
)
async def run_shell(command: str, timeout: int = 30, _confirm_fn=None) -> ToolResult:
    """Execute a shell command. Returns stdout and stderr combined.

    _confirm_fn: injected by executor context (not LLM-visible). When set to
    a callable, it is called with the command string; execution is aborted if
    it returns False. The REPL sets this to an interactive prompt function.
    """
    # Interactive confirmation (e.g. REPL sets _confirm_fn to ask the user)
    if callable(_confirm_fn):
        if not _confirm_fn(command):
            return ToolResult.error("Cancelled by user.")
    # Safety deny-list check
    cmd_lower = command.lower()
    for pattern in _BLOCKED_PATTERNS:
        if pattern in cmd_lower:
            return ToolResult.error(
                f"Blocked: command matches safety pattern '{pattern}'. "
                "This operation is not permitted."
            )

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout)
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.communicate()
            except Exception:
                pass
            return ToolResult.error(f"Command timed out after {timeout}s")

        out = stdout.decode("utf-8", errors="replace").rstrip()
        err = stderr.decode("utf-8", errors="replace").rstrip()

        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]\n{err}")
        if not parts:
            parts.append(f"(exit {proc.returncode})")
        elif proc.returncode != 0:
            parts.append(f"(exit {proc.returncode})")

        return ToolResult.ok("\n".join(parts))

    except Exception as e:
        return ToolResult.error(f"Failed to run command: {e}")
