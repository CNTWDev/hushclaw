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
    auth: str = ""
    sdk: str = ""
    docs_url: str = ""


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

    def status(self) -> dict:
        return {
            "enabled": self.enabled(),
            "configured": self.configured(),
            "name": self.manifest.name,
            "capabilities": self.manifest.capabilities,
            "auth": self.manifest.auth,
            "sdk": self.manifest.sdk,
            "docs_url": self.manifest.docs_url,
        }
