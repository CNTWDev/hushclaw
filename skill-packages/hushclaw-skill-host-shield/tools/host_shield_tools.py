"""Host Shield — whitelist-based command guard.

Policy is stored in ~/.hushclaw/shield_policy.json.
No extra dependencies required.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from hushclaw.tools.base import ToolResult, tool

_POLICY_PATH = Path("~/.hushclaw/shield_policy.json").expanduser()

_DEFAULT_POLICY: dict = {
    "allowed_prefixes": [
        "ls", "cat", "echo", "pwd", "whoami", "date", "df", "du",
        "ps", "top", "uname", "hostname", "uptime", "which", "type",
        "python", "python3", "pip", "git status", "git log", "git diff",
    ],
    "blocked_patterns": [
        r"rm\s+-rf\s+/",
        r"dd\s+if=",
        r"mkfs",
        r":\(\)\s*\{",            # fork bomb
        r"chmod\s+[0-7]*7\s+/",
        r">\s*/dev/sd",
        r"wget.+\|\s*sh",
        r"curl.+\|\s*sh",
        r"curl.+\|\s*bash",
    ],
}


def _load_policy() -> dict:
    if _POLICY_PATH.exists():
        try:
            return json.loads(_POLICY_PATH.read_text())
        except Exception:
            pass
    return dict(_DEFAULT_POLICY)


def _save_policy(policy: dict) -> None:
    _POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _POLICY_PATH.write_text(json.dumps(policy, ensure_ascii=False, indent=2))


@tool(description=(
    "Check whether a shell command is permitted by the host shield policy. "
    "Returns {allowed: bool, reason: str}. Always call this before running any shell command."
))
def shield_check_command(command: str) -> ToolResult:
    """Return allowed/blocked verdict for the given shell command string."""
    policy = _load_policy()
    cmd = command.strip()

    # Check hard-blocked patterns first
    for pat in policy.get("blocked_patterns", []):
        if re.search(pat, cmd):
            return ToolResult(output={
                "allowed": False,
                "reason": f"Matches blocked pattern: {pat}",
                "command": cmd,
            })

    # Check allowlist
    for prefix in policy.get("allowed_prefixes", []):
        if cmd == prefix or cmd.startswith(prefix + " ") or cmd.startswith(prefix + "\t"):
            return ToolResult(output={
                "allowed": True,
                "reason": f"Matches allowed prefix: {prefix}",
                "command": cmd,
            })

    return ToolResult(output={
        "allowed": False,
        "reason": "Command not in whitelist. Add it via shield_update_policy if intentional.",
        "command": cmd,
    })


@tool(description="Show the current host shield policy (allowed prefixes and blocked patterns).")
def shield_get_policy() -> ToolResult:
    """Return the full policy JSON."""
    policy = _load_policy()
    return ToolResult(output={
        "policy_file": str(_POLICY_PATH),
        "allowed_prefixes": policy.get("allowed_prefixes", []),
        "blocked_patterns": policy.get("blocked_patterns", []),
    })


@tool(description=(
    "Update the host shield policy. Pass lists of prefixes/patterns to ADD. "
    "Requires user confirmation (_confirm_fn)."
))
def shield_update_policy(
    add_allowed: list[str] | None = None,
    add_blocked: list[str] | None = None,
    remove_allowed: list[str] | None = None,
    _confirm_fn=None,
) -> ToolResult:
    """Merge changes into the policy file after user confirmation."""
    policy = _load_policy()

    summary_lines = []
    if add_allowed:
        summary_lines.append(f"ADD to whitelist: {add_allowed}")
    if add_blocked:
        summary_lines.append(f"ADD to blocklist: {add_blocked}")
    if remove_allowed:
        summary_lines.append(f"REMOVE from whitelist: {remove_allowed}")

    if not summary_lines:
        return ToolResult(error="No changes specified.")

    summary = "\n".join(summary_lines)
    if _confirm_fn and not _confirm_fn(f"Shield policy change:\n{summary}\nProceed?"):
        return ToolResult(error="Cancelled by user.")

    if add_allowed:
        existing = set(policy.get("allowed_prefixes", []))
        policy["allowed_prefixes"] = sorted(existing | set(add_allowed))
    if add_blocked:
        existing = set(policy.get("blocked_patterns", []))
        policy["blocked_patterns"] = sorted(existing | set(add_blocked))
    if remove_allowed:
        existing = set(policy.get("allowed_prefixes", []))
        policy["allowed_prefixes"] = sorted(existing - set(remove_allowed))

    _save_policy(policy)
    return ToolResult(output={"updated": True, "changes": summary_lines})
