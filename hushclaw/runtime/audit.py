"""Agent OS audit event envelope."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from hushclaw.runtime.principal import RuntimePrincipal, current_principal
from hushclaw.util.ids import make_id


@dataclass(slots=True)
class AuditEvent:
    event_type: str
    principal: RuntimePrincipal = field(default_factory=current_principal)
    session_id: str = ""
    thread_id: str = ""
    run_id: str = ""
    agent: str = ""
    resource: dict[str, Any] = field(default_factory=dict)
    approval_state: str = "none"
    source_channel: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: make_id("aud-"))
    ts: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        source = self.source_channel or self.principal.source_channel
        return {
            "event_id": self.event_id,
            "ts": self.ts,
            "principal": self.principal.to_dict(),
            "session_id": self.session_id,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "agent": self.agent,
            "event_type": self.event_type,
            "resource": dict(self.resource),
            "approval_state": self.approval_state,
            "source_channel": source,
            "metadata": dict(self.metadata),
        }


def append_audit_event(memory, event: AuditEvent, *, status: str = "completed") -> str:
    """Append an audit envelope to the existing events table when available."""
    if memory is None or not getattr(memory, "session_log", None):
        return event.event_id
    payload = event.to_dict()
    return memory.session_log.append(
        event.session_id,
        "audit:" + event.event_type,
        payload,
        thread_id=event.thread_id,
        run_id=event.run_id,
        status=status,
        event_id=event.event_id,
    )
