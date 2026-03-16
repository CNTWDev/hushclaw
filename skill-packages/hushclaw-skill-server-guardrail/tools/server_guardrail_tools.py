"""Server Guardrail — high-risk operation interceptor.

Classifies commands by risk, issues one-time tokens for HIGH-risk ops,
and writes an audit log at ~/.hushclaw/guardrail_audit.jsonl.
No extra dependencies required.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

from hushclaw.tools.base import ToolResult, tool

_AUDIT_LOG = Path("~/.hushclaw/guardrail_audit.jsonl").expanduser()

# (pattern, level, description)
_RISK_RULES: list[tuple[str, str, str]] = [
    # CRITICAL
    (r"rm\s+-rf\s+/[^/]", "CRITICAL", "Recursive delete of root-level path"),
    (r"rm\s+-rf\s+/$", "CRITICAL", "Recursive delete of filesystem root"),
    (r"dd\s+if=", "CRITICAL", "Direct disk write (dd)"),
    (r"mkfs\.", "CRITICAL", "Filesystem format"),
    (r":\(\)\s*\{.*\|.*&", "CRITICAL", "Fork bomb"),
    (r">\s*/dev/sd[a-z]", "CRITICAL", "Direct device overwrite"),
    # HIGH
    (r"\brm\b.*-[rRf]*[rf][rRf]*\b", "HIGH", "Recursive/force file deletion"),
    (r"iptables\s+(-F|-X|-Z|--flush)", "HIGH", "Flush firewall rules"),
    (r"ufw\s+(disable|reset|delete)", "HIGH", "Disable/reset firewall"),
    (r"firewall-cmd\s+.*--remove", "HIGH", "Firewall rule removal"),
    (r"systemctl\s+(stop|disable|mask)", "HIGH", "Stop/disable system service"),
    (r"chmod\s+[0-7]*[67][0-7]\s+/", "HIGH", "Permissive chmod on system path"),
    (r"chown\s+.*\s+/", "HIGH", "Ownership change on system path"),
    (r"passwd\b", "HIGH", "Password change"),
    (r"userdel|groupdel", "HIGH", "User/group deletion"),
    # MEDIUM
    (r"\bkill\s+-9\b|\bkillall\b", "MEDIUM", "Force kill process"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "MEDIUM", "System restart/shutdown"),
    (r"crontab\s+-r", "MEDIUM", "Remove all cron jobs"),
    (r"\bdropdb\b|\bdrop\s+database\b", "MEDIUM", "Drop database"),
    (r"truncate\s+--size=0", "MEDIUM", "Truncate file to zero"),
    # LOW
    (r"\brm\b\s+[^-]", "LOW", "File deletion"),
    (r"systemctl\s+restart", "LOW", "Service restart"),
]

# In-memory token store: token -> {op, issued_at}
_pending_tokens: dict[str, dict] = {}
_TOKEN_TTL = 300  # seconds


@tool(description=(
    "Assess the risk level of a shell command or operation description. "
    "Returns {level: CRITICAL|HIGH|MEDIUM|LOW|SAFE, reason: str, rules_matched: []}."
))
def guardrail_assess(command: str) -> ToolResult:
    """Classify the risk level of the given command string."""
    matched = []
    highest = "SAFE"
    order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "SAFE"]

    for pattern, level, desc in _RISK_RULES:
        if re.search(pattern, command, re.IGNORECASE):
            matched.append({"pattern": pattern, "level": level, "description": desc})
            if order.index(level) < order.index(highest):
                highest = level

    return ToolResult(output={
        "command": command,
        "level": highest,
        "rules_matched": matched,
        "requires_token": highest in ("HIGH",),
        "always_blocked": highest == "CRITICAL",
    })


@tool(description=(
    "Generate a one-time authorization token for a HIGH-risk operation. "
    "Show the token to the user and ask them to call guardrail_verify_token with it."
))
def guardrail_request_token(operation_desc: str) -> ToolResult:
    """Issue a short-lived token the user must echo back to authorize the operation."""
    # Clean up expired tokens
    now = time.time()
    expired = [k for k, v in _pending_tokens.items() if now - v["issued_at"] > _TOKEN_TTL]
    for k in expired:
        del _pending_tokens[k]

    raw = secrets.token_hex(3).upper()  # 6-char hex: easy to type
    _pending_tokens[raw] = {"operation": operation_desc, "issued_at": now}

    return ToolResult(output={
        "token": raw,
        "operation": operation_desc,
        "expires_in_seconds": _TOKEN_TTL,
        "instruction": f"To authorize this operation, call guardrail_verify_token with token='{raw}'",
    })


@tool(description="Verify the authorization token the user typed. Returns {authorized: bool}.")
def guardrail_verify_token(token: str) -> ToolResult:
    """Check that the supplied token matches a pending authorization request."""
    key = token.strip().upper()
    entry = _pending_tokens.get(key)
    if not entry:
        return ToolResult(output={"authorized": False, "reason": "Token not found or already used."})

    elapsed = time.time() - entry["issued_at"]
    if elapsed > _TOKEN_TTL:
        del _pending_tokens[key]
        return ToolResult(output={"authorized": False, "reason": "Token expired."})

    op = entry["operation"]
    del _pending_tokens[key]
    return ToolResult(output={
        "authorized": True,
        "operation": op,
        "message": "Token verified. You may proceed with the operation.",
    })


@tool(description="Append an entry to the guardrail audit log (~/.hushclaw/guardrail_audit.jsonl).")
def guardrail_audit_log(action: str, status: str, detail: str = "") -> ToolResult:
    """Write one JSONL line to the audit log."""
    _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "action": action,
        "status": status,
        "detail": detail,
    }
    with _AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return ToolResult(output={"logged": True, "record": record})
