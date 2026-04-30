"""Centralized runtime policy checks for tool execution."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from hushclaw.tools.base import ToolDefinition

# Patterns checked against shell commands before execution.
# Each entry is a compiled regex; any match blocks the call.
_BLOCKED_SHELL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+/"),   # rm -rf /  and variants
    re.compile(r"rm\s+-[a-zA-Z]*f[a-zA-Z]*r?\s+/"),   # rm -fr /  variant
    re.compile(r"rm\s+-rf\s+~/"),                       # rm -rf ~/
    re.compile(r">\s*/dev/(s|h)da"),                    # overwrite raw disk
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\b.*\bif="),
    re.compile(r":\(\)\s*\{"),                          # fork bomb
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"),
]

# Absolute path prefixes that delete_file must not touch.
_BLOCKED_DELETE_PREFIXES = (
    "/etc/", "/bin/", "/sbin/", "/usr/bin/", "/usr/sbin/",
    "/lib/", "/lib64/", "/boot/", "/dev/", "/proc/", "/sys/",
    "/var/", "/run/",
)


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""
    requires_confirmation: bool = False
    annotations: dict[str, Any] = field(default_factory=dict)


class PolicyGate:
    """Small first-step policy gate for tool execution.

    This centralizes the most important runtime checks so sensitive tools do
    not rely exclusively on tool-local guardrails.
    """

    def check(
        self,
        td: ToolDefinition,
        arguments: dict[str, Any],
        runtime_context,
    ) -> PolicyDecision:
        tool_name = td.name

        if tool_name == "run_shell":
            command = str((arguments or {}).get("command") or "")
            for pattern in _BLOCKED_SHELL_PATTERNS:
                if pattern.search(command):
                    return PolicyDecision(
                        allowed=False,
                        reason=(
                            f"Blocked by runtime policy: command matches dangerous pattern '{pattern.pattern}'."
                        ),
                    )
            confirm_fn = runtime_context.get("_confirm_fn") if runtime_context is not None else None
            if callable(confirm_fn) and not confirm_fn(command):
                return PolicyDecision(
                    allowed=False,
                    reason="Cancelled by user.",
                    requires_confirmation=True,
                )

        elif tool_name == "delete_file":
            path = str((arguments or {}).get("path") or "")
            for prefix in _BLOCKED_DELETE_PREFIXES:
                if path.startswith(prefix):
                    return PolicyDecision(
                        allowed=False,
                        reason=f"Blocked by runtime policy: deleting '{path}' is not permitted.",
                    )

        return PolicyDecision(allowed=True)
