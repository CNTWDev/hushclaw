"""Unified extension lifecycle contract for Agent OS."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ExtensionManifest:
    id: str
    kind: str
    name: str
    description: str = ""
    capabilities: tuple[str, ...] = ()
    auth_requirements: tuple[str, ...] = ()
    runtime_kind: str = ""
    scope_support: tuple[str, ...] = ("personal",)
    tool_definitions: tuple[str, ...] = ()
    status_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExtensionResult:
    ok: bool
    extension_id: str
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExtensionStatus:
    extension_id: str
    kind: str
    enabled: bool = False
    installed: bool = True
    configured: bool = True
    running: bool = False
    scope: str = "personal"
    metadata: dict[str, Any] = field(default_factory=dict)


class Extension(Protocol):
    def manifest(self) -> ExtensionManifest: ...
    def install(self, scope: str = "personal") -> ExtensionResult: ...
    def enable(self, principal=None, scope: str = "personal") -> ExtensionResult: ...
    def disable(self, principal=None, scope: str = "personal") -> ExtensionResult: ...
    def status(self, principal=None, scope: str = "personal") -> ExtensionStatus: ...
    def uninstall(self, scope: str = "personal") -> ExtensionResult: ...


class ReadOnlyExtension:
    """Base adapter for extensions whose lifecycle is owned elsewhere today."""

    def __init__(self, manifest: ExtensionManifest, status: ExtensionStatus | None = None) -> None:
        self._manifest = manifest
        self._status = status or ExtensionStatus(extension_id=manifest.id, kind=manifest.kind)

    def manifest(self) -> ExtensionManifest:
        return self._manifest

    def install(self, scope: str = "personal") -> ExtensionResult:
        return ExtensionResult(True, self._manifest.id, "Already installed.", {"scope": scope})

    def enable(self, principal=None, scope: str = "personal") -> ExtensionResult:
        return ExtensionResult(False, self._manifest.id, "Enable is managed by the existing subsystem.", {"scope": scope})

    def disable(self, principal=None, scope: str = "personal") -> ExtensionResult:
        return ExtensionResult(False, self._manifest.id, "Disable is managed by the existing subsystem.", {"scope": scope})

    def status(self, principal=None, scope: str = "personal") -> ExtensionStatus:
        self._status.scope = scope
        return self._status

    def uninstall(self, scope: str = "personal") -> ExtensionResult:
        return ExtensionResult(False, self._manifest.id, "Uninstall is managed by the existing subsystem.", {"scope": scope})
