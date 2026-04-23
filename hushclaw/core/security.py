"""Credential redaction and data classification utilities.

Provides redact_credentials() as the single entry point for stripping
secrets from error messages, logs, and any user-facing strings.

Patterns covered:
  - Anthropic / OpenAI API keys  (sk-...)
  - HTTP Bearer tokens           (Bearer <token>)
  - Generic api_key assignments  (api_key=..., apikey: ...)
  - Authorization headers        (Authorization: <value>)

This module uses only stdlib — zero additional dependencies.
"""
from __future__ import annotations

import re

_CRED_RE = re.compile(
    r"("
    r"sk-[A-Za-z0-9\-_]{10,}"                           # Anthropic/OpenAI keys
    r"|Bearer\s+[A-Za-z0-9\-_.+/]{8,}"                  # HTTP Bearer tokens
    r"|api[_\-]?key\s*[:=]\s*[A-Za-z0-9\-_.]{8,}"       # api_key=... / apikey: ...
    r"|[Aa]uthorization\s*:\s*[A-Za-z0-9\-_.+/ ]{8,}"   # Authorization: ...
    r")",
    re.IGNORECASE,
)


def redact_credentials(text: str) -> str:
    """Replace credential patterns in *text* with ``[REDACTED]``."""
    return _CRED_RE.sub("[REDACTED]", text)
