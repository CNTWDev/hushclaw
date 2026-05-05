"""Lifecycle hook bus for AgentLoop runtime events."""
from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from hushclaw.util.logging import get_logger

log = get_logger("runtime.hooks")

HookHandler = Callable[["HookEvent"], Any | Awaitable[Any]]


@dataclass(slots=True)
class _RegisteredHook:
    handler: HookHandler
    background: bool = False


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
        self._handlers: dict[str, list[_RegisteredHook]] = defaultdict(list)

    def on(self, event_name: str, handler: HookHandler, *, background: bool = False) -> None:
        """Register a handler for an event name or ``"*"` for all events.

        Foreground handlers keep the historical behavior: ``emit()`` waits for
        them before returning.  Background handlers are best-effort side effects
        and are scheduled without blocking the agent loop.
        """
        self._handlers[event_name].append(_RegisteredHook(handler, background))

    def handlers_for(self, event_name: str) -> list[HookHandler]:
        """Return handlers registered for a specific event name."""
        return [h.handler for h in self._handlers.get(event_name, ())]

    async def _run_handler(self, event: HookEvent, handler: HookHandler) -> None:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            log.warning("hook %s handler failed: %s", event.name, exc, exc_info=True)

    async def emit(self, event_name: str, **payload: Any) -> None:
        """Dispatch a lifecycle event to all matching handlers."""
        event = HookEvent(name=event_name, payload=payload)
        handlers = [
            *self._handlers.get(event_name, ()),
            *self._handlers.get("*", ()),
        ]
        for registered in handlers:
            if registered.background:
                try:
                    asyncio.create_task(
                        self._run_handler(event, registered.handler),
                        name=f"hook:{event_name}",
                    )
                except RuntimeError:
                    await self._run_handler(event, registered.handler)
                continue
            await self._run_handler(event, registered.handler)
