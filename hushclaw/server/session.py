"""server/session.py — session-level task / subscriber decoupling types.

_SessionEntry and _SessionSink are shared between server_impl.py and
chat_mixin.py to avoid circular imports.

Phase 8: _SessionSink now writes wire-format events to the durable events
table (via _SessionEntry.memory) so reconnect replay reads from the event
log rather than an in-memory deque.  The deque is retained as a hot-cache
fallback (maxlen=50) for when memory is unavailable (tests, cold start).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field as _dc_field
from typing import TYPE_CHECKING

from hushclaw.util.ids import make_id

if TYPE_CHECKING:
    from hushclaw.memory.store import MemoryStore


# ── Module-level session constants ─────────────────────────────────────────────

_BUFFER_LIMIT = 50    # hot-cache fallback — events table is the primary replay store
_SESSION_TTL  = 1800  # seconds to retain a finished session entry (30 min)

# Wire event types that are meaningful for reconnect replay
_REPLAY_EVENTS = frozenset({
    "session", "tool_call", "tool_result", "done", "error",
    "round_info", "session_status", "compaction", "pipeline_step", "awaiting_user",
})

# Prefix used when persisting wire events to the events table
_WIRE_PREFIX = "ws:"


# ── Session entry ──────────────────────────────────────────────────────────────

@dataclass
class _SessionEntry:
    """Server-level session state that outlives any single WebSocket connection."""

    session_id: str
    task: object = None            # asyncio.Task | None
    memory: object = None          # MemoryStore | None — for durable event writes
    buffer: deque = _dc_field(default_factory=lambda: deque(maxlen=_BUFFER_LIMIT))
    text: str = ""                 # accumulated response text from streaming chunks
    subscriber: object = None      # current WebSocket | None
    created_at: float = _dc_field(default_factory=time.time)
    finished_at: float | None = None
    pending_wire_events: list[tuple[str, dict]] = _dc_field(default_factory=list)
    wire_flush_task: object = None  # asyncio.Task | None

    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()


# ── Session sink ───────────────────────────────────────────────────────────────

class _SessionSink:
    """
    Duck-typed WebSocket proxy passed to streaming handlers in place of a real ws.

    For each incoming event:
    • Accumulates ``chunk`` text into SessionEntry.text.
    • Writes replay-worthy events to the durable events table (ws: prefix)
      when SessionEntry.memory is available — the events table is the primary
      replay store.  Falls back to the in-memory buffer when memory is None.
    • Forwards every message to the current subscriber (live WebSocket).

    Subscriber failures are swallowed and the subscriber field cleared.
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
                mem = self._entry.memory
                if mem is not None:
                    self._entry.pending_wire_events.append((_WIRE_PREFIX + t, evt))
                    self._ensure_wire_flush_task()
                else:
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

    def _ensure_wire_flush_task(self) -> None:
        task = self._entry.wire_flush_task
        if task is not None and not task.done():
            return
        try:
            self._entry.wire_flush_task = asyncio.create_task(
                _flush_wire_events(self._entry),
                name=f"session-wire-flush:{self._entry.session_id[:12]}",
            )
        except RuntimeError:
            _flush_wire_events_sync(self._entry)


async def _flush_wire_events(entry: _SessionEntry) -> None:
    # Small debounce batches bursts such as tool_call/tool_result/done.
    await asyncio.sleep(0.05)
    _flush_wire_events_sync(entry)


def _flush_wire_events_sync(entry: _SessionEntry) -> None:
    mem = entry.memory
    if mem is None or not entry.pending_wire_events:
        return
    batch = list(entry.pending_wire_events)
    entry.pending_wire_events.clear()
    ts = int(time.time() * 1000)
    try:
        conn = mem.conn
        conn.executemany(
            "INSERT INTO events "
            "(event_id, session_id, thread_id, run_id, step_id, type, payload_json, artifact_id, status, ts) "
            "VALUES (?, ?, '', '', '', ?, ?, '', 'completed', ?)",
            [
                (
                    make_id("ev-"),
                    entry.session_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    ts,
                )
                for event_type, payload in batch
            ],
        )
        conn.commit()
    except Exception:
        # DB write failed — preserve in hot-cache buffer for live reconnects.
        for _, payload in batch:
            try:
                entry.buffer.append(json.dumps(payload, ensure_ascii=False))
            except Exception:
                pass
