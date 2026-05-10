"""Runtime lifecycle helpers."""

from hushclaw.runtime.hooks import HookBus, HookEvent
from hushclaw.runtime.interaction import InteractionGate
from hushclaw.runtime.principal import (
    RuntimePrincipal,
    SINGLE_USER_PRINCIPAL,
    current_principal,
    principal_context,
)

__all__ = [
    "HookBus",
    "HookEvent",
    "InteractionGate",
    "RuntimePrincipal",
    "SINGLE_USER_PRINCIPAL",
    "RuntimeServices",
    "current_principal",
    "principal_context",
]


def __getattr__(name: str):
    if name == "RuntimeServices":
        from hushclaw.runtime.services import RuntimeServices

        return RuntimeServices
    raise AttributeError(name)
