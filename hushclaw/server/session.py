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
from hushclaw.util.logging import get_logger

if TYPE_CHECKING:
    from hushclaw.memory.store import MemoryStore


# ── Module-level session constants ─────────────────────────────────────────────

_BUFFER_LIMIT = 50    # hot-cache fallback — events table is the primary replay store
_SESSION_TTL  = 1800  # seconds to retain a finished session entry (30 min)
_MAX_PENDING_AMENDMENTS = 5
_WIRE_PENDING_SOFT_LIMIT = 64
_WIRE_PENDING_HARD_LIMIT = 256

# Wire event types that are meaningful for reconnect replay
_REPLAY_EVENTS = frozenset({
    "session", "tool_call", "tool_result", "done", "error",
    "round_info", "session_status", "session_runtime", "child_run_state_changed",
    "compaction", "pipeline_step", "awaiting_user",
    "run_state_changed", "thread_state_changed", "step_state_changed",
    "user_amendment_queued", "user_amendment_applied",
    "research_job_started", "research_queries_planned", "research_search_progress",
    "research_read_progress", "research_job_completed", "research_job_failed",
    "background_job_started", "background_job_completed", "background_job_failed",
    "background_job_resumed",
})

# Prefix used when persisting wire events to the events table
_WIRE_PREFIX = "ws:"

log = get_logger("server.session")


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
    last_progress_at: int = 0
    lease_expires_at: int = 0
    last_progress_kind: str = ""
    stale_since: int = 0
    stall_count: int = 0


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
    mutex: object = _dc_field(default_factory=asyncio.Lock)
    generation: int = 0
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

    def prepare_for_new_request(self, memory: object = None) -> tuple[object, object]:
        """Reset mutable per-run state and return tasks that should be cancelled."""
        cancel_task = self.task if self.task is not None and not self.task.done() else None
        flush_task = self.wire_flush_task if self.wire_flush_task is not None and not self.wire_flush_task.done() else None
        self.generation += 1
        if memory is not None:
            self.memory = memory
        self.task = None
        self.text = ""
        self.buffer.clear()
        self.pending_wire_events.clear()
        self.wire_flush_task = None
        self.finished_at = None
        self.pending_amendments.clear()
        self.applied_amendment = None
        self.active_run_id = ""
        self.current_request = None
        self.runtime_run = type(self.runtime_run)()
        return cancel_task, flush_task

    def is_active_generation(self, generation: int) -> bool:
        return int(generation or 0) == int(self.generation or 0)

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
            last_progress_at=int(time.time() * 1000),
        )

    def touch_child_run(
        self,
        run_id: str,
        *,
        progress_kind: str = "",
        lease_expires_at: int = 0,
        stale: bool | None = None,
    ) -> None:
        child = self.child_runs.get(str(run_id or ""))
        if child is None:
            return
        now_ms = int(time.time() * 1000)
        child.updated_at = now_ms
        child.last_progress_at = now_ms
        if progress_kind:
            child.last_progress_kind = str(progress_kind)
        if lease_expires_at > 0:
            child.lease_expires_at = int(lease_expires_at)
        if stale is False:
            child.stale_since = 0
            if child.state == "stale":
                child.state = "running"

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
        if state == "stale":
            if not child.stale_since:
                child.stale_since = child.updated_at
            child.stall_count += 1
        elif state:
            child.stale_since = 0

    def complete_child_run(self, run_id: str, *, state: str = "completed", summary: str = "") -> None:
        child = self.child_runs.get(str(run_id or ""))
        if child is None:
            return
        child.state = str(state or "completed")
        if summary:
            child.summary = str(summary)
        child.active_step = _RuntimeStepState()
        child.updated_at = int(time.time() * 1000)
        child.lease_expires_at = 0
        child.stale_since = 0

    _ACTIVE_CHILD_STATES = frozenset({"queued", "running", "waiting_user", "paused", "stale"})
    _TERMINAL_MAIN_STATES = frozenset({"idle", "completed", "stopped"})
    _CHILD_PRIORITY = ("waiting_user", "stale", "running", "queued", "paused")

    def effective_display_status(self, base_status: str) -> str:
        """Return the status the UI should actually display.

        When the main run is terminal but background child runs are still
        active, the parent's 'Done'/'Completed' is premature — promote the
        display to the most urgent child state instead.
        """
        if base_status not in self._TERMINAL_MAIN_STATES:
            return base_status
        active = [r for r in self.child_runs.values() if r.state in self._ACTIVE_CHILD_STATES]
        if not active:
            return base_status
        active_states = {r.state for r in active}
        for priority in self._CHILD_PRIORITY:
            if priority in active_states:
                return priority
        return "running"

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
        amendment["queue_limited"] = False
        if len(self.pending_amendments) < _MAX_PENDING_AMENDMENTS:
            self.pending_amendments.append(amendment)
            return amendment
        tail = self.pending_amendments[-1]
        prior_id = str(tail.get("amendment_id") or "").strip()
        merged_ids = []
        if isinstance(tail.get("merged_amendment_ids"), list):
            merged_ids.extend(
                str(item).strip()
                for item in tail["merged_amendment_ids"]
                if str(item or "").strip()
            )
        if prior_id:
            merged_ids.append(prior_id)
        merged_ids.append(str(amendment.get("amendment_id") or "").strip())
        texts = [str(tail.get("text") or "").strip(), str(amendment.get("text") or "").strip()]
        tail["text"] = "\n\n".join(part for part in texts if part)
        if isinstance(amendment.get("images"), list):
            tail["images"] = list(tail.get("images") or []) + [
                str(item) for item in amendment["images"] if str(item or "").strip()
            ]
        if isinstance(amendment.get("references"), list):
            tail["references"] = list(tail.get("references") or []) + [
                item for item in amendment["references"] if isinstance(item, dict)
            ]
        for key in ("agent", "workspace", "client_now", "client_turn_id"):
            if amendment.get(key):
                tail[key] = amendment.get(key)
        tail["amendment_id"] = str(amendment.get("amendment_id") or tail.get("amendment_id") or "")
        tail["amendment_seq"] = amendment["amendment_seq"]
        tail["queued_at"] = amendment["queued_at"]
        tail["queue_limited"] = True
        tail["merged_amendment_ids"] = list(dict.fromkeys(item for item in merged_ids if item))
        return dict(tail)

    def pop_merged_amendment(self) -> dict | None:
        if not self.pending_amendments:
            return None
        items = list(self.pending_amendments)
        self.pending_amendments.clear()
        latest = dict(items[-1] or {})
        merged_ids = [
            str(item.get("amendment_id") or "").strip()
            for item in items
            if str(item.get("amendment_id") or "").strip()
        ]
        for item in items:
            merged_ids.extend(
                str(mid).strip()
                for mid in (item.get("merged_amendment_ids") or [])
                if str(mid or "").strip()
            )
        latest["merged_amendment_ids"] = list(dict.fromkeys(merged_ids))
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
                    "last_progress_at": item.last_progress_at,
                    "lease_expires_at": item.lease_expires_at,
                    "last_progress_kind": item.last_progress_kind,
                    "stale_since": item.stale_since,
                    "stall_count": item.stall_count,
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

    __slots__ = ("_entry", "_generation")

    def __init__(self, entry: _SessionEntry, *, generation: int | None = None) -> None:
        self._entry = entry
        self._generation = int(entry.generation if generation is None else generation)

    async def send(self, raw: str) -> None:
        if not self._entry.is_active_generation(self._generation):
            return
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
                    if len(self._entry.pending_wire_events) >= _WIRE_PENDING_HARD_LIMIT:
                        log.warning(
                            "session sink pending wire backlog overflow; forcing flush: session=%s generation=%s queued=%d type=%s",
                            self._entry.session_id,
                            self._generation,
                            len(self._entry.pending_wire_events),
                            t,
                        )
                        _flush_wire_events_sync(self._entry)
                    self._entry.pending_wire_events.append((_WIRE_PREFIX + t, evt))
                    if len(self._entry.pending_wire_events) >= _WIRE_PENDING_SOFT_LIMIT:
                        self._ensure_wire_flush_task()
                    self._ensure_wire_flush_task()
                else:
                    self._entry.buffer.append(raw)
        except Exception as exc:
            log.warning(
                "SessionSink buffer error: session=%s generation=%s error=%s raw_length=%d",
                self._entry.session_id,
                self._generation,
                exc,
                len(str(raw or "")),
            )

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
        log.warning(
            "session wire flush failed; falling back to hot buffer: session=%s pending=%d",
            entry.session_id,
            len(batch),
            exc_info=True,
        )
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
