"""HushClaw exception hierarchy."""


class HushClawError(Exception):
    """Base exception for all HushClaw errors."""


class ConfigError(HushClawError):
    """Configuration loading or validation error."""


class ProviderError(HushClawError):
    """LLM provider communication error.

    Carries an optional HTTP *status_code* so :func:`~hushclaw.core.errors.classify_error`
    can classify by status before falling back to regex string matching.

    Providers should raise ``ProviderError(message, status_code=429)`` whenever the
    HTTP response code is available.  Providers that raise generic ``Exception`` still
    work — classification falls back to regex patterns.
    """

    def __init__(self, message: str = "", status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code: int | None = status_code


class ToolError(HushClawError):
    """Tool registration or execution error."""


class MemoryError(HushClawError):
    """Memory storage or retrieval error."""


class ContextLimitError(HushClawError):
    """Context window limit exceeded and compaction failed."""
