"""HushClaw exception hierarchy."""


class HushClawError(Exception):
    """Base exception for all HushClaw errors."""


class ConfigError(HushClawError):
    """Configuration loading or validation error."""


class ProviderError(HushClawError):
    """LLM provider communication error."""


class ToolError(HushClawError):
    """Tool registration or execution error."""


class MemoryError(HushClawError):
    """Memory storage or retrieval error."""


class ContextLimitError(HushClawError):
    """Context window limit exceeded and compaction failed."""
