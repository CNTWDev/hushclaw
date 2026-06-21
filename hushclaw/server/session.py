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
    "round_info", "session_status", "session_runtime", "child_run_state_changed",
    "compaction", "pipeline_step", "awaiting_user",
    "run_state_changed", "thread_state_changed", "step_state_changed",
    "user_amendment_queued", "user_amendment_applied",
    "research_job_started", "research_queries_planned", "research_search_progress",
    "research_read_progress", "research_job_completed", "research_job_failed",
})

# Prefix used when persisting wire events to the events table
_WIRE_PREFIX = "ws:"


# ── Session entry ──────────────────────────────────────────────────────────────

@dataclass
class _RuntimeStepState:
    step_id: str = ""
    step_type: str = ""
    state: str = ""
    summary: str = ""
    meta: dict = _dc_field(default_factory=dict)


@dataclass
class _RuntimeRunState:
    run_id: str = ""
    trigger_type: str = "user"
    state: str = ""
    request: dict | None = None
    active_step: _RuntimeStepState = _dc_field(default_factory=_RuntimeStepState)


@dataclass
class _RuntimeThreadState:
    thread_id: str = ""
    agent_name: str = ""
    state: str = "active"


@dataclass
class _RuntimeChildRunState:
    run_id: str = ""
    thread_id: str = ""
    parent_run_id: str = ""
    agent_name: str = ""
    trigger_type: str = "sub_agent"
    run_kind: str = "child"
    visibility: str = "background"
    state: str = ""
    summary: str = ""
    active_step: _RuntimeStepState = _dc_field(default_factory=_RuntimeStepState)
    updated_at: int = 0


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
    pending_amendments: list[dict] = _dc_field(default_factory=list)
    applied_amendment: dict | None = None
    run_seq: int = 0
    amendment_seq: int = 0
    active_run_id: str = ""
    last_completed_run_id: str = ""
    last_superseded_run_id: str = ""
    current_request: dict | None = None
    runtime_thread: _RuntimeThreadState = _dc_field(default_factory=_RuntimeThreadState)
    runtime_run: _RuntimeRunState = _dc_field(default_factory=_RuntimeRunState)
    child_runs: dict[str, _RuntimeChildRunState] = _dc_field(default_factory=dict)

    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def bind_thread(self, thread_id: str, *, agent_name: str = "") -> None:
        if thread_id:
            self.runtime_thread.thread_id = str(thread_id)
        if agent_name:
            self.runtime_thread.agent_name = str(agent_name)
        self.runtime_thread.state = "active"

    def begin_run(self, payload: dict | None = None, *, run_id: str = "", trigger_type: str = "user") -> str:
        self.run_seq += 1
        self.active_run_id = str(run_id or make_id("run-"))
        self.current_request = dict(payload or {})
        self.runtime_run = _RuntimeRunState(
            run_id=self.active_run_id,
            trigger_type=str(trigger_type or "user"),
            state="running",
            request=dict(payload or {}),
        )
        return self.active_run_id

    def complete_run(self, run_id: str, *, superseded: bool = False, state: str = "") -> None:
        if not run_id:
            return
        if superseded:
            self.last_superseded_run_id = run_id
        self.last_completed_run_id = run_id
        if self.runtime_run.run_id == run_id:
            self.runtime_run.state = state or ("superseded" if superseded else "completed")
            self.runtime_run.active_step = _RuntimeStepState()
        if self.active_run_id == run_id:
            self.active_run_id = ""
            self.current_request = None

    def set_step(
        self,
        *,
        step_type: str,
        step_id: str,
        state: str,
        summary: str,
        meta: dict | None = None,
    ) -> None:
        self.runtime_run.active_step = _RuntimeStepState(
            step_id=str(step_id or ""),
            step_type=str(step_type or ""),
            state=str(state or ""),
            summary=str(summary or ""),
            meta=dict(meta or {}),
        )

    def register_child_run(
        self,
        *,
        run_id: str,
        thread_id: str = "",
        parent_run_id: str = "",
        agent_name: str = "",
        trigger_type: str = "sub_agent",
        run_kind: str = "child",
        visibility: str = "background",
        state: str = "running",
        summary: str = "",
    ) -> None:
        if not run_id:
            return
        self.child_runs[run_id] = _RuntimeChildRunState(
            run_id=str(run_id),
            thread_id=str(thread_id or ""),
            parent_run_id=str(parent_run_id or ""),
            agent_name=str(agent_name or ""),
            trigger_type=str(trigger_type or "sub_agent"),
            run_kind=str(run_kind or "child"),
            visibility=str(visibility or "background"),
            state=str(state or "running"),
            summary=str(summary or ""),
            updated_at=int(time.time() * 1000),
        )

    def set_child_run_state(
        self,
        run_id: str,
        *,
        state: str = "",
        summary: str = "",
        step_id: str = "",
        step_type: str = "",
        step_state: str = "",
        meta: dict | None = None,
    ) -> None:
        child = self.child_runs.get(str(run_id or ""))
        if child is None:
            return
        if state:
            child.state = str(state)
        if summary:
            child.summary = str(summary)
        if step_id or step_type or step_state or meta:
            child.active_step = _RuntimeStepState(
                step_id=str(step_id or child.active_step.step_id),
                step_type=str(step_type or child.active_step.step_type),
                state=str(step_state or child.active_step.state),
                summary=str(summary or child.active_step.summary),
                meta=dict(meta or child.active_step.meta or {}),
            )
        child.updated_at = int(time.time() * 1000)

    def complete_child_run(self, run_id: str, *, state: str = "completed", summary: str = "") -> None:
        child = self.child_runs.get(str(run_id or ""))
        if child is None:
            return
        child.state = str(state or "completed")
        if summary:
            child.summary = str(summary)
        child.active_step = _RuntimeStepState()
        child.updated_at = int(time.time() * 1000)

    def clear_step(self, *, step_id: str = "") -> None:
        current = self.runtime_run.active_step
        if step_id and current.step_id and current.step_id != step_id:
            return
        self.runtime_run.active_step = _RuntimeStepState()

    def queue_amendment(self, payload: dict) -> dict:
        amendment = dict(payload or {})
        self.amendment_seq += 1
        amendment.setdefault("amendment_id", make_id("amd-"))
        amendment["amendment_seq"] = self.amendment_seq
        amendment["queued_at"] = int(time.time() * 1000)
        self.pending_amendments.append(amendment)
        return amendment

    def pop_merged_amendment(self) -> dict | None:
        if not self.pending_amendments:
            return None
        items = list(self.pending_amendments)
        self.pending_amendments.clear()
        latest = dict(items[-1] or {})
        latest["merged_amendment_ids"] = [
            str(item.get("amendment_id") or "").strip()
            for item in items
            if str(item.get("amendment_id") or "").strip()
        ]
        latest["queued_count"] = len(items)
        texts = [str(item.get("text") or "").strip() for item in items if str(item.get("text") or "").strip()]
        if texts:
            latest["text"] = "\n\n".join(texts)
        images: list[str] = []
        references: list[dict] = []
        for item in items:
            if isinstance(item.get("images"), list):
                images.extend(str(v) for v in item["images"] if str(v or "").strip())
            if isinstance(item.get("references"), list):
                references.extend(v for v in item["references"] if isinstance(v, dict))
        latest["images"] = images
        latest["references"] = references
        self.applied_amendment = latest
        return latest

    def runtime_meta(self) -> dict:
        amendment = self.applied_amendment or {}
        child_runs = sorted(
            self.child_runs.values(),
            key=lambda item: (item.updated_at, item.run_id),
            reverse=True,
        )
        return {
            "thread_id": self.runtime_thread.thread_id,
            "thread_state": self.runtime_thread.state,
            "thread_agent": self.runtime_thread.agent_name,
            "run_id": self.active_run_id,
            "run_seq": self.run_seq,
            "run_state": self.runtime_run.state,
            "trigger_type": self.runtime_run.trigger_type,
            "pending_amendments": len(self.pending_amendments),
            "last_completed_run_id": self.last_completed_run_id,
            "last_superseded_run_id": self.last_superseded_run_id,
            "last_amendment_id": str(amendment.get("amendment_id") or ""),
            "active_step": {
                "step_id": self.runtime_run.active_step.step_id,
                "step_type": self.runtime_run.active_step.step_type,
                "state": self.runtime_run.active_step.state,
                "summary": self.runtime_run.active_step.summary,
                "meta": dict(self.runtime_run.active_step.meta or {}),
            },
            "child_runs": [
                {
                    "run_id": item.run_id,
                    "thread_id": item.thread_id,
                    "parent_run_id": item.parent_run_id,
                    "agent_name": item.agent_name,
                    "trigger_type": item.trigger_type,
                    "run_kind": item.run_kind,
                    "visibility": item.visibility,
                    "state": item.state,
                    "summary": item.summary,
                    "updated_at": item.updated_at,
                    "active_step": {
                        "step_id": item.active_step.step_id,
                        "step_type": item.active_step.step_type,
                        "state": item.active_step.state,
                        "summary": item.active_step.summary,
                        "meta": dict(item.active_step.meta or {}),
                    },
                }
                for item in child_runs[:8]
            ],
        }


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
            if t == "done":
                # Keep the authoritative final response for reconnect replay.
                self._entry.text = str(evt.get("text", "") or "")
            if t in _REPLAY_EVENTS:
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


async def publish_session_event(entry: _SessionEntry, event: dict) -> None:
    """Publish a structured event through the session sink path."""
    if entry is None or not isinstance(event, dict):
        return
    payload = dict(event)
    payload.setdefault("session_id", entry.session_id)
    payload.setdefault("ts", int(time.time() * 1000))
    await _SessionSink(entry).send(json.dumps(payload, ensure_ascii=False))
