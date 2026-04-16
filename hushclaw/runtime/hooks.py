"""Lifecycle hook bus for AgentLoop runtime events."""
from __future__ import annotations

import inspect
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from hushclaw.util.logging import get_logger

log = get_logger("runtime.hooks")

HookHandler = Callable[["HookEvent"], Any | Awaitable[Any]]


@dataclass(slots=True)
class HookEvent:
    """Structured lifecycle event emitted by the runtime."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)


class HookBus:
    """Best-effort async dispatcher for runtime lifecycle hooks.

    Hook failures are isolated and logged so that instrumentation, policy checks,
    and future lifecycle extensions don't interrupt the main agent flow.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = defaultdict(list)

    def on(self, event_name: str, handler: HookHandler) -> None:
        """Register a handler for an event name or ``"*"` for all events."""
        self._handlers[event_name].append(handler)

    def handlers_for(self, event_name: str) -> list[HookHandler]:
        """Return handlers registered for a specific event name."""
        return list(self._handlers.get(event_name, ()))

    async def emit(self, event_name: str, **payload: Any) -> None:
        """Dispatch a lifecycle event to all matching handlers."""
        event = HookEvent(name=event_name, payload=payload)
        handlers = [
            *self._handlers.get(event_name, ()),
            *self._handlers.get("*", ()),
        ]
        for handler in handlers:
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                log.warning("hook %s handler failed: %s", event_name, exc, exc_info=True)
