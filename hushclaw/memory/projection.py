"""ProjectionWorker: event-driven async projections (after_turn, future: belief consolidation).

Instead of calling after_turn() synchronously from _background_finalize, the
ProjectionWorker polls the events table for assistant_message_emitted events it
hasn't seen yet, then dispatches to registered handlers.

Key properties:
- Decoupled from harness lifecycle: survives loop crashes and restarts
- Idempotent: all handlers must tolerate duplicate calls (after_turn already does)
- Non-blocking: runs as a background asyncio task, never on the critical path
- Resumable: cursor position persisted in the projections table
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING

from hushclaw.util.logging import get_logger

if TYPE_CHECKING:
    from hushclaw.context.engine import ContextEngine
    from hushclaw.memory.store import MemoryStore

log = get_logger("projection")

_POLL_INTERVAL = 0.5   # seconds between event polls
_BATCH_SIZE    = 20    # events per poll cycle


class ProjectionWorker:
    """Async worker that builds derived data (projections) from the events table."""

    def __init__(
        self,
        memory: "MemoryStore",
        context_engine: "ContextEngine",
    ) -> None:
        self._memory = memory
        self._engine = context_engine
        self._task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background polling task (idempotent).

        Safe to call from sync context — silently defers if no event loop is running;
        the next call from within an async context will start the worker.
        """
        if self._task is not None and not self._task.done():
            return
        self._running = True
        coro = self._run()
        try:
            self._task = asyncio.create_task(coro, name="projection-worker")
        except RuntimeError:
            coro.close()
            self._running = False

    async def stop(self) -> None:
        """Stop the worker and wait for it to finish."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while self._running:
            try:
                await self._process_pending()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ProjectionWorker error: %s", exc)
            await asyncio.sleep(_POLL_INTERVAL)

    async def _process_pending(self) -> None:
        last_ts, last_event_id = self._get_cursor("after_turn")
        rows = self._memory.conn.execute(
            "SELECT event_id, session_id, ts, payload_json "
            "FROM events "
            "WHERE type='assistant_message_emitted' "
            "  AND (ts > ? OR (ts = ? AND event_id > ?)) "
            "ORDER BY ts ASC, event_id ASC "
            "LIMIT ?",
            (last_ts, last_ts, last_event_id, _BATCH_SIZE),
        ).fetchall()
        for row in rows:
            await self._handle_assistant_emitted(
                row["session_id"], row["ts"], row["event_id"], row["payload_json"],
            )
            self._advance_cursor("after_turn", row["ts"], row["event_id"])

    async def _handle_assistant_emitted(
        self,
        session_id: str,
        event_ts_ms: int,
        event_id: str,
        payload_json: str,
    ) -> None:
        """Run after_turn for the turn corresponding to this event."""
        payload: dict = {}
        try:
            payload = json.loads(payload_json or "{}")
        except Exception:
            pass

        user_input = ""
        assistant_response = ""

        user_turn_id = payload.get("user_turn_id", "")
        asst_turn_id = payload.get("assistant_turn_id", "")

        if user_turn_id and asst_turn_id:
            # Fast path: payload carries explicit turn IDs — no ts-based guessing.
            row = self._memory.conn.execute(
                "SELECT content FROM turns WHERE turn_id=?", (asst_turn_id,)
            ).fetchone()
            if row:
                assistant_response = row["content"]
            row = self._memory.conn.execute(
                "SELECT content FROM turns WHERE turn_id=?", (user_turn_id,)
            ).fetchone()
            if row:
                user_input = row["content"]
        else:
            # Legacy fallback: approximate lookup by session + timestamp.
            event_ts_sec = event_ts_ms // 1000
            turns = self._memory.conn.execute(
                "SELECT role, content FROM turns "
                "WHERE session=? AND ts <= ? "
                "ORDER BY ts DESC LIMIT 6",
                (session_id, event_ts_sec),
            ).fetchall()
            for t in turns:
                role, content = t["role"], t["content"]
                if not assistant_response and role == "assistant":
                    assistant_response = content
                elif assistant_response and not user_input and role == "user":
                    user_input = content
                    break

        if not (user_input or assistant_response):
            return

        try:
            await self._engine.after_turn(
                session_id, user_input, assistant_response, self._memory
            )
        except Exception as exc:
            log.warning(
                "after_turn projection failed for session %s: %s", session_id[:8], exc
            )

    # ------------------------------------------------------------------
    # Cursor persistence
    # ------------------------------------------------------------------

    def _get_cursor(self, name: str) -> tuple[int, str]:
        """Return (last_ts, last_event_id) for the named projection cursor."""
        row = self._memory.conn.execute(
            "SELECT last_ts, last_event_id FROM projections WHERE name=?", (name,)
        ).fetchone()
        if row:
            return row["last_ts"], (row["last_event_id"] or "")
        return 0, ""

    def _advance_cursor(self, name: str, ts: int, event_id: str) -> None:
        now = int(time.time())
        self._memory.conn.execute(
            "INSERT INTO projections (name, last_ts, last_event_id, updated) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "  last_ts=excluded.last_ts, "
            "  last_event_id=excluded.last_event_id, "
            "  updated=excluded.updated",
            (name, ts, event_id, now),
        )
        self._memory.conn.commit()
