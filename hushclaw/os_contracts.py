"""Platform-neutral contracts shared by Agent OS ingress adapters."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time
from typing import Any

from hushclaw.util.ids import make_id


@dataclass(frozen=True, slots=True)
class ConversationAddress:
    provider: str
    conversation_id: str
    account_id: str = ""
    thread_id: str = ""

    def key(self) -> tuple[str, str, str, str]:
        return (self.provider, self.account_id, self.conversation_id, self.thread_id)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ConversationBinding:
    address: ConversationAddress
    session_id: str
    workspace: str = ""
    agent: str = ""
    external_user_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.address.provider,
            "account_id": self.address.account_id,
            "conversation_id": self.address.conversation_id,
            "thread_id": self.address.thread_id,
            "session_id": self.session_id,
            "workspace": self.workspace,
            "agent": self.agent,
            "external_user_id": self.external_user_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class AgentOSMessageRequest:
    """Normalized inbound message accepted by the Agent OS boundary."""

    agent: str
    text: str
    session_id: str
    workspace: str = ""
    client_now: str = ""
    source_channel: str = ""
    principal_id: str = ""
    auth_context: dict[str, Any] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    session_entry: Any = None
    pipeline_run_id: str = ""
    parent_thread_id: str = ""
    parent_run_id: str = ""
    trigger_type: str = ""
    run_kind: str = "primary"
    visibility: str = "foreground"


@dataclass(frozen=True, slots=True)
class AgentOSEvent:
    """Canonical Agent OS event while preserving the existing wire payload."""

    event_id: str
    event_type: str
    session_id: str
    source_channel: str
    payload: dict[str, Any]
    thread_id: str = ""
    run_id: str = ""
    step_id: str = ""
    created_ms: int = 0
    schema_version: int = 1

    @classmethod
    def from_wire(
        cls,
        event: dict[str, Any],
        *,
        session_id: str,
        source_channel: str = "",
    ) -> "AgentOSEvent":
        payload = dict(event or {})
        return cls(
            event_id=str(payload.get("event_id") or make_id("ose-")),
            event_type=str(payload.get("type") or "unknown"),
            session_id=str(payload.get("session_id") or session_id),
            source_channel=str(payload.get("source_channel") or source_channel),
            thread_id=str(payload.get("thread_id") or ""),
            run_id=str(payload.get("run_id") or ""),
            step_id=str(payload.get("step_id") or ""),
            created_ms=int(payload.get("created_ms") or time.time() * 1000),
            payload=payload,
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            **self.payload,
            "type": self.event_type,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "source_channel": self.source_channel,
            "created_ms": self.created_ms,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class AgentOSOutboundMessage:
    address: ConversationAddress
    body: str
    session_id: str = ""
    message_type: str = "text"
    idempotency_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    delivery_id: str
    status: str
    external_message_id: str = ""
    error: str = ""
