"""Agent OS extension lifecycle contracts."""

from hushclaw.extensions.base import (
    ExtensionManifest,
    ExtensionResult,
    ExtensionStatus,
    ReadOnlyExtension,
)
from hushclaw.extensions.registry import ExtensionRegistry

__all__ = [
    "ExtensionManifest",
    "ExtensionRegistry",
    "ExtensionResult",
    "ExtensionStatus",
    "ReadOnlyExtension",
]
