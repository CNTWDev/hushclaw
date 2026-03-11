"""GhostClaw exception hierarchy."""


class GhostClawError(Exception):
    """Base exception for all GhostClaw errors."""


class ConfigError(GhostClawError):
    """Configuration loading or validation error."""


class ProviderError(GhostClawError):
    """LLM provider communication error."""


class ToolError(GhostClawError):
    """Tool registration or execution error."""


class MemoryError(GhostClawError):
    """Memory storage or retrieval error."""


class ContextLimitError(GhostClawError):
    """Context window limit exceeded and compaction failed."""
