"""Extension discovery adapters for existing HushClaw subsystems."""
from __future__ import annotations

from hushclaw.extensions.base import ExtensionManifest, ExtensionStatus, ReadOnlyExtension


class ExtensionRegistry:
    """Aggregates skills, app connectors, channel connectors, and agents."""

    def __init__(self, gateway=None) -> None:
        self.gateway = gateway

    def list(self) -> list[dict]:
        return [
            self._payload(ext)
            for ext in [
                *self._skill_extensions(),
                *self._app_connector_extensions(),
                *self._channel_connector_extensions(),
                *self._agent_extensions(),
            ]
        ]

    @staticmethod
    def _payload(ext: ReadOnlyExtension) -> dict:
        manifest = ext.manifest()
        status = ext.status()
        return {
            "manifest": {
                "id": manifest.id,
                "kind": manifest.kind,
                "name": manifest.name,
                "description": manifest.description,
                "capabilities": list(manifest.capabilities),
                "auth_requirements": list(manifest.auth_requirements),
                "runtime_kind": manifest.runtime_kind,
                "scope_support": list(manifest.scope_support),
                "tool_definitions": list(manifest.tool_definitions),
                "status_schema": dict(manifest.status_schema),
            },
            "status": {
                "extension_id": status.extension_id,
                "kind": status.kind,
                "enabled": status.enabled,
                "installed": status.installed,
                "configured": status.configured,
                "running": status.running,
                "scope": status.scope,
                "metadata": dict(status.metadata),
            },
        }

    def _skill_extensions(self) -> list[ReadOnlyExtension]:
        agent = getattr(self.gateway, "base_agent", None)
        registry = getattr(agent, "_skill_registry", None)
        if not registry:
            return []
        items = registry.list_all()
        out = []
        for item in items:
            name = str(item.get("name") or "")
            out.append(ReadOnlyExtension(
                ExtensionManifest(
                    id=f"skill:{name}",
                    kind="skill",
                    name=name,
                    description=str(item.get("description") or ""),
                    capabilities=tuple(item.get("tags") or ()),
                    runtime_kind="prompt_bundle",
                    scope_support=("personal", "workspace"),
                    tool_definitions=tuple([item.get("direct_tool")] if item.get("direct_tool") else []),
                ),
                ExtensionStatus(
                    extension_id=f"skill:{name}",
                    kind="skill",
                    enabled=item.get("enabled", True),
                    configured=item.get("available", True),
                    metadata={"scope": item.get("scope") or item.get("tier") or "user"},
                ),
            ))
        return out

    def _app_connector_extensions(self) -> list[ReadOnlyExtension]:
        agent = getattr(self.gateway, "base_agent", None)
        config = getattr(getattr(agent, "config", None), "app_connectors", None)
        if config is None:
            return []
        try:
            from hushclaw.app_connectors import AppConnectorRegistry
            registry = AppConnectorRegistry(config)
        except Exception:
            return []
        out = []
        for key, data in registry.status().items():
            out.append(ReadOnlyExtension(
                ExtensionManifest(
                    id=f"app_connector:{key}",
                    kind="app_connector",
                    name=str(data.get("name") or key),
                    description="External workspace connector.",
                    capabilities=tuple(data.get("capabilities") or ()),
                    auth_requirements=tuple([data.get("auth")] if data.get("auth") else []),
                    runtime_kind="tool_injection",
                    scope_support=("personal", "workspace", "org"),
                ),
                ExtensionStatus(
                    extension_id=f"app_connector:{key}",
                    kind="app_connector",
                    enabled=bool(data.get("enabled")),
                    configured=bool(data.get("configured")),
                    metadata=data,
                ),
            ))
        return out

    def _channel_connector_extensions(self) -> list[ReadOnlyExtension]:
        manager = getattr(getattr(self.gateway, "_server", None), "_connectors_manager", None)
        statuses = manager.status() if manager is not None else {}
        out = []
        for key, running in statuses.items():
            out.append(ReadOnlyExtension(
                ExtensionManifest(
                    id=f"channel_connector:{key}",
                    kind="channel_connector",
                    name=key.title(),
                    description="Inbound channel connector.",
                    runtime_kind="long_running_process",
                    scope_support=("personal", "workspace", "org"),
                ),
                ExtensionStatus(
                    extension_id=f"channel_connector:{key}",
                    kind="channel_connector",
                    enabled=True,
                    configured=True,
                    running=bool(running),
                ),
            ))
        return out

    def _agent_extensions(self) -> list[ReadOnlyExtension]:
        if not self.gateway:
            return []
        out = []
        for agent in self.gateway.list_agents():
            name = str(agent.get("name") or "")
            out.append(ReadOnlyExtension(
                ExtensionManifest(
                    id=f"agent:{name}",
                    kind="agent",
                    name=name,
                    description=str(agent.get("description") or ""),
                    capabilities=tuple(agent.get("capabilities") or ()),
                    runtime_kind="agent_loop",
                    scope_support=("personal", "workspace", "org"),
                ),
                ExtensionStatus(
                    extension_id=f"agent:{name}",
                    kind="agent",
                    enabled=True,
                    configured=True,
                    running=True,
                    metadata=agent,
                ),
            ))
        return out
