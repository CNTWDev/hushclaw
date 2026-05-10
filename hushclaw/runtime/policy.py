"""Centralized runtime policy checks for tool execution."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

from hushclaw.tools.base import ToolDefinition

if TYPE_CHECKING:
    from hushclaw.runtime.principal import RuntimePrincipal

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

    Distros inject predicates via install_rules() at assembly time.
    Hard-coded shell/fs safeguards always run regardless of distro rules.
    """

    def __init__(self) -> None:
        self._tool_rule: Callable[[str, RuntimePrincipal], bool] | None = None
        self._memory_rule: Callable[[str, RuntimePrincipal], bool] | None = None
        self._connector_rule: Callable[[str, RuntimePrincipal], bool] | None = None

    def install_rules(
        self,
        *,
        can_call_tool: Callable[[str, RuntimePrincipal], bool] | None = None,
        can_read_memory: Callable[[str, RuntimePrincipal], bool] | None = None,
        can_use_connector: Callable[[str, RuntimePrincipal], bool] | None = None,
    ) -> None:
        """Install distro-provided policy predicates. Called by DistroRuntime.assemble()."""
        if can_call_tool is not None:
            self._tool_rule = can_call_tool
        if can_read_memory is not None:
            self._memory_rule = can_read_memory
        if can_use_connector is not None:
            self._connector_rule = can_use_connector

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

        principal = (
            runtime_context.effective_principal()
            if runtime_context is not None and hasattr(runtime_context, "effective_principal")
            else None
        )
        if self._tool_rule is not None and not self._tool_rule(tool_name, principal):
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' blocked by distro policy.",
            )
        return PolicyDecision(
            allowed=True,
            annotations={
                "principal_id": getattr(principal, "principal_id", "local-user"),
                "source_channel": getattr(principal, "source_channel", "local"),
                "tool": tool_name,
                "mutating": bool(getattr(td, "mutating", False)),
            },
        )

    def can_call_tool(self, principal, td: ToolDefinition, arguments: dict[str, Any]) -> PolicyDecision:
        """Capability-aware policy check — distro rules evaluated first."""
        if self._tool_rule is not None and not self._tool_rule(td.name, principal):
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{td.name}' blocked by distro policy.",
            )
        return PolicyDecision(
            allowed=True,
            annotations={
                "principal_id": getattr(principal, "principal_id", "local-user"),
                "tool": td.name,
                "mutating": bool(getattr(td, "mutating", False)),
            },
        )

    def can_read_memory(self, principal, scope: str) -> PolicyDecision:
        if self._memory_rule is not None and not self._memory_rule(scope, principal):
            return PolicyDecision(allowed=False, reason=f"Memory scope '{scope}' blocked by distro policy.")
        return PolicyDecision(allowed=True, annotations={"scope": scope})

    def can_write_memory(self, principal, scope: str) -> PolicyDecision:
        return PolicyDecision(allowed=True, annotations={"scope": scope})

    def can_use_connector(self, principal, connector_id: str) -> PolicyDecision:
        if self._connector_rule is not None and not self._connector_rule(connector_id, principal):
            return PolicyDecision(allowed=False, reason=f"Connector '{connector_id}' blocked by distro policy.")
        return PolicyDecision(allowed=True, annotations={"connector_id": connector_id})

    def requires_approval(self, principal, action: str, resource: dict[str, Any] | None = None) -> bool:
        return False
