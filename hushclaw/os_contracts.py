"""Platform-neutral contracts shared by Agent OS ingress adapters."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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
