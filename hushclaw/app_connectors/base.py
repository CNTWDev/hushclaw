"""Base protocol for outbound app connectors."""
from __future__ import annotations

from dataclasses import dataclass, field

from hushclaw.tools.base import ToolDefinition


@dataclass(frozen=True)
class ConnectorManifest:
    id: str
    name: str
    description: str
    capabilities: list[str] = field(default_factory=list)


class AppConnector:
    manifest: ConnectorManifest

    def __init__(self, config, secrets) -> None:
        self.config = config
        self.secrets = secrets

    def configured(self) -> bool:
        return False

    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", False))

    def tools(self) -> list[ToolDefinition]:
        return []
