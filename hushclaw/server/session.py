"""server/session.py — session-level task / subscriber decoupling types.

_SessionEntry and _SessionSink are shared between server_impl.py and
chat_mixin.py to avoid circular imports.
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field as _dc_field


# ── Module-level session constants ─────────────────────────────────────────────

_BUFFER_LIMIT = 400   # max events buffered per live session
_SESSION_TTL  = 1800  # seconds to retain a finished session entry (30 min)

# Only buffer events that are meaningful for reconnect replay
_REPLAY_EVENTS = frozenset({
    "session", "tool_call", "tool_result", "done", "error",
    "round_info", "session_status", "compaction", "pipeline_step",
})


# ── Session entry ──────────────────────────────────────────────────────────────

@dataclass
class _SessionEntry:
    """Server-level session state that outlives any single WebSocket connection."""

    session_id: str
    task: object = None            # asyncio.Task | None
    buffer: deque = _dc_field(default_factory=lambda: deque(maxlen=_BUFFER_LIMIT))
    text: str = ""                 # accumulated response text from streaming chunks
    subscriber: object = None      # current WebSocket | None
    created_at: float = _dc_field(default_factory=time.time)
    finished_at: float | None = None

    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()


# ── Session sink ───────────────────────────────────────────────────────────────

class _SessionSink:
    """
    Duck-typed WebSocket proxy passed to streaming handlers in place of a real ws.

    • Buffers replay-worthy events into the SessionEntry.
    • Accumulates ``chunk`` text into SessionEntry.text.
    • Forwards every message to the current subscriber when one is attached.
    Subscriber failures are swallowed and the subscriber is cleared.
    """

    __slots__ = ("_entry",)

    def __init__(self, entry: _SessionEntry) -> None:
        self._entry = entry

    async def send(self, raw: str) -> None:
        try:
            evt = json.loads(raw)
            t = evt.get("type", "")
            if t == "chunk":
                self._entry.text += evt.get("text", "")
            elif t in _REPLAY_EVENTS:
                self._entry.buffer.append(raw)
        except Exception:
            pass

        sub = self._entry.subscriber
        if sub is not None:
            try:
                await sub.send(raw)
            except Exception:
                self._entry.subscriber = None

    @property
    def remote_address(self):
        sub = self._entry.subscriber
        return getattr(sub, "remote_address", "background") if sub else "background"
