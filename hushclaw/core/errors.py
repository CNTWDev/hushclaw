"""Structured error classification for provider calls.

Replaces ad-hoc ``str(e).lower()`` keyword checks with a typed
``ErrorRecovery`` dataclass so every call site handles errors consistently.

Usage::

    try:
        response = await provider.complete(...)
    except Exception as exc:
        recovery = classify_error(exc)
        if recovery.should_compress:
            messages = compact(messages)
            continue          # retry same round
        if recovery.retryable:
            await asyncio.sleep(backoff(attempt))
            continue
        raise

Categories
----------
TRANSIENT
    Timeout, connection reset, 429 rate-limit, 5xx server error.
    Action: exponential back-off retry, no context change.

CONTEXT_TOO_LONG
    Context window exceeded (400 with "too many tokens" / "context length").
    Action: compact context, then retry.

AUTH_FAILURE
    401 / 403, invalid key.
    Action: surface to user; no point retrying with the same key.

FATAL
    All other errors (validation, bad request, etc.).
    Action: raise immediately.

Classification order
--------------------
When the exception is a :class:`~hushclaw.exceptions.ProviderError` with a
``status_code``, the HTTP status code is checked *first* (precise and fast).
Regex matching on the message string is used as a fallback for providers that
raise generic exceptions or that do not populate ``status_code``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Keyword tables (fallback — used when no status_code is available)
# ---------------------------------------------------------------------------

_TRANSIENT_RE = re.compile(
    r"timeout|timed[_ ]out|rate.?limit|429|500|502|503|504|"
    r"connection.?(reset|refused|error)|overloaded|temporarily unavailable",
    re.IGNORECASE,
)

_CONTEXT_LENGTH_RE = re.compile(
    r"too (many|large|long)|context.?(length|window|size|limit)|"
    r"maximum.?(context|token|length)|reduce (the length|your message|input|context)|"
    r"prompt is too (long|large)|"
    r"This model's maximum context length",
    re.IGNORECASE,
)

_AUTH_RE = re.compile(
    r"\b(401|403|unauthorized|forbidden|invalid.?api.?key|"
    r"authentication.?fail|api key.*invalid)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# ErrorRecovery
# ---------------------------------------------------------------------------

@dataclass
class ErrorRecovery:
    """Structured recovery advice for a provider exception."""

    retryable: bool
    """True if the caller should retry after a back-off delay."""

    should_compress: bool
    """True if the context should be compacted before retrying."""

    is_auth_failure: bool
    """True when the API key or credentials are invalid (401/403)."""

    message: str
    """Human-readable explanation of the error category."""

    original: Exception | None = field(default=None, repr=False)
    """The original exception, preserved for logging."""


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def _classify_by_status(status: int, exc: Exception) -> ErrorRecovery | None:
    """Return an ErrorRecovery based on HTTP status code, or None if unrecognised."""
    msg = str(exc)
    if status in (401, 403):
        return ErrorRecovery(
            retryable=False, should_compress=False, is_auth_failure=True,
            message=f"Authentication failure (HTTP {status}) — check your API key: {msg}",
            original=exc,
        )
    if status == 429:
        return ErrorRecovery(
            retryable=True, should_compress=False, is_auth_failure=False,
            message=f"Rate-limited (HTTP 429) — will retry with back-off: {msg}",
            original=exc,
        )
    if status in (500, 502, 503, 504):
        return ErrorRecovery(
            retryable=True, should_compress=False, is_auth_failure=False,
            message=f"Transient server error (HTTP {status}) — will retry: {msg}",
            original=exc,
        )
    if status == 400 and _CONTEXT_LENGTH_RE.search(msg):
        return ErrorRecovery(
            retryable=True, should_compress=True, is_auth_failure=False,
            message=f"Context too long (HTTP 400) — will compact and retry: {msg}",
            original=exc,
        )
    return None


def classify_error(exc: Exception) -> ErrorRecovery:
    """Classify *exc* and return a structured recovery plan.

    Raises nothing — always returns an ``ErrorRecovery``.

    Checks HTTP status code first (when available via ``ProviderError.status_code``),
    then falls back to regex matching on the exception message string.
    """
    # --- Tier 1: status-code classification (precise, provider-agnostic) ---
    from hushclaw.exceptions import ProviderError  # noqa: PLC0415 (avoid circular at module level)
    if isinstance(exc, ProviderError) and exc.status_code is not None:
        result = _classify_by_status(exc.status_code, exc)
        if result is not None:
            return result

    # --- Tier 2: regex fallback (handles generic Exception from providers) ---
    msg = str(exc)

    if _AUTH_RE.search(msg):
        return ErrorRecovery(
            retryable=False,
            should_compress=False,
            is_auth_failure=True,
            message=f"Authentication failure — check your API key: {msg}",
            original=exc,
        )

    if _CONTEXT_LENGTH_RE.search(msg):
        return ErrorRecovery(
            retryable=True,
            should_compress=True,
            is_auth_failure=False,
            message=f"Context too long — will compact and retry: {msg}",
            original=exc,
        )

    if _TRANSIENT_RE.search(msg):
        return ErrorRecovery(
            retryable=True,
            should_compress=False,
            is_auth_failure=False,
            message=f"Transient provider error — will retry: {msg}",
            original=exc,
        )

    return ErrorRecovery(
        retryable=False,
        should_compress=False,
        is_auth_failure=False,
        message=f"Non-retryable provider error: {msg}",
        original=exc,
    )


def backoff(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """Exponential back-off: ``base * 2^attempt``, capped at *cap* seconds."""
    return min(base * (2 ** attempt), cap)
