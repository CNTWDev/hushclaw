"""Runtime identity context for Agent OS boundaries."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimePrincipal:
    """Actor identity for one runtime request.

    Personal/local mode uses a stable owner principal so kernel code can depend
    on identity being present without requiring auth infrastructure.
    """

    principal_id: str = "local-user"
    org_id: str = ""
    workspace_id: str = ""
    roles: tuple[str, ...] = ("owner",)
    mode: str = "personal"
    source_channel: str = "local"
    auth_context: dict[str, Any] = field(default_factory=dict)

    @property
    def is_owner(self) -> bool:
        return "owner" in self.roles

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "org_id": self.org_id,
            "workspace_id": self.workspace_id,
            "roles": list(self.roles),
            "mode": self.mode,
            "source_channel": self.source_channel,
            "auth_context": dict(self.auth_context),
        }


SINGLE_USER_PRINCIPAL = RuntimePrincipal()

_current_principal: ContextVar[RuntimePrincipal] = ContextVar(
    "hushclaw_current_principal",
    default=SINGLE_USER_PRINCIPAL,
)


def current_principal() -> RuntimePrincipal:
    return _current_principal.get()


def set_current_principal(principal: RuntimePrincipal) -> Token:
    return _current_principal.set(principal)


def reset_current_principal(token: Token) -> None:
    _current_principal.reset(token)


class principal_context:
    """Context manager that installs a principal for the current request."""

    def __init__(self, principal: RuntimePrincipal | None = None) -> None:
        self.principal = principal or SINGLE_USER_PRINCIPAL
        self._token: Token | None = None

    def __enter__(self) -> RuntimePrincipal:
        self._token = set_current_principal(self.principal)
        return self.principal

    def __exit__(self, *_exc) -> None:
        if self._token is not None:
            reset_current_principal(self._token)
