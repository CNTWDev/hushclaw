"""Agent OS service facade.

Product shells should move toward this boundary instead of importing kernel
objects directly. The facade is intentionally thin for v1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hushclaw.extensions import ExtensionRegistry
from hushclaw.memory.ports import SQLiteMemoryPort
from hushclaw.runtime.audit import AuditEvent
from hushclaw.runtime.principal import RuntimePrincipal, current_principal
from hushclaw.tools.base import to_api_schema


@dataclass(slots=True)
class AgentOSService:
    gateway: Any

    @property
    def principal(self) -> RuntimePrincipal:
        return current_principal()

    def list_agents(self) -> list[dict]:
        return self.gateway.list_agents()

    def list_tools(self) -> list[dict]:
        registry = self.gateway.base_agent.registry
        return [to_api_schema(td) for td in registry.list_tools()]

    def list_extensions(self) -> list[dict]:
        return ExtensionRegistry(self.gateway).list()

    def memory_port(self) -> SQLiteMemoryPort:
        return SQLiteMemoryPort(self.gateway.memory)

    def search_memory(self, query: str, *, scopes: list[str] | None = None, limit: int = 5) -> list[dict]:
        return self.memory_port().search(query, scopes=scopes, principal=self.principal, limit=limit)

    def remember(
        self,
        content: str,
        *,
        scope: str = "global",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self.memory_port().remember(content, scope=scope, principal=self.principal, metadata=metadata)

    def audit_events(self, *, session_id: str = "", limit: int = 200) -> list[dict]:
        mem = self.gateway.memory
        if session_id:
            events = mem.events.session_events(session_id, limit=limit)
        else:
            rows = mem.conn.execute(
                "SELECT event_id, session_id, thread_id, run_id, step_id, type, payload_json, artifact_id, status, ts "
                "FROM events WHERE type LIKE 'audit:%' ORDER BY ts DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            from hushclaw.memory.events import _row_to_dict
            events = [_row_to_dict(r) for r in rows]
        return [e for e in events if str(e.get("type", "")).startswith("audit:")]

    def build_audit_event(self, event_type: str, **kwargs: Any) -> AuditEvent:
        return AuditEvent(event_type=event_type, principal=self.principal, **kwargs)
