"""EventStore: append-only event log for session/thread/run replay."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time

from hushclaw.util.ids import make_id


_conn_locks: dict[int, threading.Lock] = {}
_conn_locks_mu = threading.Lock()


def _conn_lock(conn: sqlite3.Connection) -> threading.Lock:
    """Return the threading.Lock bound to this connection (created lazily).

    sqlite3.Connection is a C type with no __dict__, so we key by id()
    in a module-level dict.  Shared by EventStore and MemoryStore so async
    writes to the same connection are serialized across thread-pool workers.
    """
    key = id(conn)
    try:
        return _conn_locks[key]
    except KeyError:
        with _conn_locks_mu:
            if key not in _conn_locks:
                _conn_locks[key] = threading.Lock()
            return _conn_locks[key]


class EventStore:
    """Write and query the events table.

    All writes are immediately committed (WAL mode set at DB open).
    Callers use the pending→completed/failed pattern for operations that
    can fail mid-execution (e.g. tool calls):

        eid = store.append(..., status="pending")
        try:
            result = await execute(...)
            store.complete(eid, {"result": result[:500]})
        except Exception as e:
            store.fail(eid, str(e))
    """

    __slots__ = ("conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def append(
        self,
        session_id: str,
        event_type: str,
        payload: dict,
        *,
        thread_id: str = "",
        run_id: str = "",
        step_id: str = "",
        artifact_id: str = "",
        status: str = "completed",
        event_id: str | None = None,
    ) -> str:
        """Write an event and return its event_id."""
        eid = event_id or make_id("ev-")
        ts = int(time.time() * 1000)
        self.conn.execute(
            "INSERT INTO events "
            "(event_id, session_id, thread_id, run_id, step_id, type, payload_json, artifact_id, status, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, session_id, thread_id, run_id, step_id, event_type,
             json.dumps(payload, ensure_ascii=False), artifact_id, status, ts),
        )
        self.conn.commit()
        return eid

    def complete(self, event_id: str, payload_update: dict | None = None) -> None:
        """Mark a pending event as completed, merging payload_update into original payload.

        Read-merge-write: fields from the pending event's original payload (tool name,
        call_id, input params) are preserved; payload_update keys override on conflict.
        If payload_update contains a non-empty 'artifact_id', the events.artifact_id
        column is also updated so RetentionExecutor's orphan-artifact query works.
        """
        if payload_update is not None:
            row = self.conn.execute(
                "SELECT payload_json FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            original: dict = {}
            if row:
                try:
                    original = json.loads(row[0] or "{}")
                except Exception:
                    pass
            merged = {**original, **payload_update}
            aid = merged.get("artifact_id", "") or ""
            self.conn.execute(
                "UPDATE events SET status='completed', payload_json=?, artifact_id=? WHERE event_id=?",
                (json.dumps(merged, ensure_ascii=False), aid, event_id),
            )
        else:
            self.conn.execute(
                "UPDATE events SET status='completed' WHERE event_id=?", (event_id,)
            )
        self.conn.commit()

    def fail(self, event_id: str, error: str) -> None:
        """Mark a pending event as failed, preserving original payload with error field."""
        row = self.conn.execute(
            "SELECT payload_json FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        if row is None:
            return
        try:
            payload = json.loads(row[0])
        except Exception:
            payload = {}
        payload["error"] = error[:500]
        self.conn.execute(
            "UPDATE events SET status='failed', payload_json=? WHERE event_id=?",
            (json.dumps(payload, ensure_ascii=False), event_id),
        )
        self.conn.commit()

    def session_events(self, session_id: str, limit: int = 1000) -> list[dict]:
        """Return all events for a session ordered by ts ascending."""
        rows = self.conn.execute(
            "SELECT event_id, session_id, thread_id, run_id, step_id, type, "
            "payload_json, artifact_id, status, ts "
            "FROM events WHERE session_id=? ORDER BY ts ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def thread_events(self, thread_id: str, limit: int = 500) -> list[dict]:
        """Return all events for a thread ordered by ts ascending."""
        rows = self.conn.execute(
            "SELECT event_id, session_id, thread_id, run_id, step_id, type, "
            "payload_json, artifact_id, status, ts "
            "FROM events WHERE thread_id=? ORDER BY ts ASC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def session_wire_events(self, session_id: str, limit: int = 1000) -> list[str]:
        """Return wire-format raw JSON strings for WebSocket reconnect replay.

        These are events stored by _SessionSink with type prefix 'ws:'.
        Ordered ascending so the client receives them in original send order.
        """
        rows = self.conn.execute(
            "SELECT payload_json FROM events "
            "WHERE session_id=? AND type LIKE 'ws:%' "
            "ORDER BY ts ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [row[0] for row in rows]

    # ── Async variants (non-blocking for asyncio callers) ──────────────────

    async def aappend(self, *args, **kwargs) -> str:
        """Non-blocking append: runs in a thread-pool worker."""
        _lock = _conn_lock(self.conn)
        def _do() -> str:
            with _lock:
                return self.append(*args, **kwargs)
        return await asyncio.to_thread(_do)

    async def acomplete(self, event_id: str, payload_update: dict | None = None) -> None:
        """Non-blocking complete: runs in a thread-pool worker."""
        _lock = _conn_lock(self.conn)
        def _do() -> None:
            with _lock:
                self.complete(event_id, payload_update)
        await asyncio.to_thread(_do)

    async def afail(self, event_id: str, error: str) -> None:
        """Non-blocking fail: runs in a thread-pool worker."""
        _lock = _conn_lock(self.conn)
        def _do() -> None:
            with _lock:
                self.fail(event_id, error)
        await asyncio.to_thread(_do)


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["payload"] = json.loads(d.pop("payload_json") or "{}")
    except Exception:
        d["payload"] = {}
    return d
