"""SessionLog: explicit event-log facade for sessions, threads, and runs."""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from hushclaw.memory.events import EventStore, _row_to_dict
from hushclaw.providers.base import Message

if TYPE_CHECKING:
    from collections.abc import Iterable


class SessionLog:
    """Thin facade over the append-only events table.

    This gives the architecture a named boundary for "raw session facts" while
    preserving the existing EventStore write path. Callers can keep using
    ``append/complete/fail`` while newer code can depend on higher-level query
    methods such as ``events_by_thread()`` or ``events_in_window()``.
    """

    __slots__ = ("conn", "_events")

    def __init__(self, conn: sqlite3.Connection, event_store: EventStore | None = None) -> None:
        self.conn = conn
        self._events = event_store or EventStore(conn)

    def append(self, *args, **kwargs):
        return self._events.append(*args, **kwargs)

    def complete(self, *args, **kwargs) -> None:
        self._events.complete(*args, **kwargs)

    def fail(self, *args, **kwargs) -> None:
        self._events.fail(*args, **kwargs)

    def session_events(self, session_id: str, limit: int = 1000) -> list[dict]:
        return self.events_by_session(session_id, limit=limit)

    def thread_events(self, thread_id: str, limit: int = 500) -> list[dict]:
        return self.events_by_thread(thread_id, limit=limit)

    def run_events(self, run_id: str, limit: int = 500) -> list[dict]:
        return self.events_by_run(run_id, limit=limit)

    def session_wire_events(self, session_id: str, limit: int = 1000) -> list[str]:
        return self._events.session_wire_events(session_id, limit=limit)

    def events_by_session(
        self,
        session_id: str,
        *,
        limit: int = 1000,
        since_ts_ms: int | None = None,
        until_ts_ms: int | None = None,
    ) -> list[dict]:
        return self._query_events(
            "session_id=?",
            (session_id,),
            limit=limit,
            since_ts_ms=since_ts_ms,
            until_ts_ms=until_ts_ms,
        )

    def events_by_thread(
        self,
        thread_id: str,
        *,
        limit: int = 500,
        since_ts_ms: int | None = None,
        until_ts_ms: int | None = None,
    ) -> list[dict]:
        return self._query_events(
            "thread_id=?",
            (thread_id,),
            limit=limit,
            since_ts_ms=since_ts_ms,
            until_ts_ms=until_ts_ms,
        )

    def events_by_run(
        self,
        run_id: str,
        *,
        limit: int = 500,
        since_ts_ms: int | None = None,
        until_ts_ms: int | None = None,
    ) -> list[dict]:
        return self._query_events(
            "run_id=?",
            (run_id,),
            limit=limit,
            since_ts_ms=since_ts_ms,
            until_ts_ms=until_ts_ms,
        )

    def events_in_window(
        self,
        *,
        session_id: str = "",
        thread_id: str = "",
        run_id: str = "",
        since_ts_ms: int | None = None,
        until_ts_ms: int | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[object] = []
        for clause, value in (
            ("session_id=?", session_id),
            ("thread_id=?", thread_id),
            ("run_id=?", run_id),
        ):
            if value:
                clauses.append(clause)
                params.append(value)
        where = " AND ".join(clauses) if clauses else "1=1"
        return self._query_events(
            where,
            tuple(params),
            limit=limit,
            since_ts_ms=since_ts_ms,
            until_ts_ms=until_ts_ms,
        )

    def replay_context(
        self,
        *,
        session_id: str = "",
        thread_id: str = "",
        limit: int = 10_000,
    ) -> list[Message]:
        """Rebuild message context from append-only session events.

        When ``thread_id`` is provided, replay is thread-scoped. Otherwise it
        falls back to session scope, which matches the legacy restore behavior.
        """
        if thread_id:
            events = self.events_by_thread(thread_id, limit=limit)
        elif session_id:
            events = self.events_by_session(session_id, limit=limit)
        else:
            return []

        rebuilt: list[Message] = []
        for event in events:
            payload = event.get("payload") or {}
            event_type = event.get("type")

            if event_type == "user_message_received":
                text = str(payload.get("input") or "")
                if text:
                    rebuilt.append(Message(role="user", content=text))
                continue

            if event_type == "assistant_message_emitted":
                text = str(payload.get("text") or "")
                if text:
                    rebuilt.append(Message(role="assistant", content=text))
                continue

            if event_type == "tool_call_requested" and event.get("status") in {"completed", "failed"}:
                tool_name = str(payload.get("tool") or "")
                call_id = str(payload.get("call_id") or "")
                result_text = str(payload.get("result") or "")
                if not result_text and event.get("status") == "failed":
                    result_text = str(payload.get("error") or "")
                if result_text:
                    rebuilt.append(
                        Message(
                            role="tool",
                            content=result_text,
                            tool_call_id=call_id or None,
                            tool_name=tool_name or None,
                        )
                    )

        return rebuilt

    def replay_turns(
        self,
        *,
        session_id: str = "",
        thread_id: str = "",
        limit: int = 10_000,
    ) -> list[dict]:
        """Rebuild a turn-like transcript from append-only session events.

        This is intended for UI/history readers that want a stable transcript
        view without directly depending on the legacy ``turns`` table.
        """
        if thread_id:
            events = self.events_by_thread(thread_id, limit=limit)
        elif session_id:
            events = self.events_by_session(session_id, limit=limit)
        else:
            return []

        rebuilt: list[dict] = []
        for event in events:
            payload = event.get("payload") or {}
            event_type = event.get("type")
            ts = int(event.get("ts") or 0)

            if event_type == "user_message_received":
                text = str(payload.get("input") or "")
                if text:
                    rebuilt.append({
                        "role": "user",
                        "content": text,
                        "tool_name": "",
                        "ts": ts,
                        "source_event_id": event.get("event_id", ""),
                    })
                continue

            if event_type == "assistant_message_emitted":
                text = str(payload.get("text") or "")
                if text:
                    rebuilt.append({
                        "role": "assistant",
                        "content": text,
                        "tool_name": "",
                        "ts": ts,
                        "input_tokens": int(payload.get("input_tokens") or 0),
                        "output_tokens": int(payload.get("output_tokens") or 0),
                        "source_event_id": event.get("event_id", ""),
                    })
                continue

            if event_type == "tool_call_requested" and event.get("status") in {"completed", "failed"}:
                text = str(payload.get("result") or "")
                if not text and event.get("status") == "failed":
                    text = str(payload.get("error") or "")
                if text:
                    rebuilt.append({
                        "role": "tool",
                        "content": text,
                        "tool_name": str(payload.get("tool") or ""),
                        "tool_call_id": str(payload.get("call_id") or ""),
                        "ts": ts,
                        "source_event_id": event.get("event_id", ""),
                    })

        return rebuilt

    def replay_token_totals(
        self,
        *,
        session_id: str = "",
        thread_id: str = "",
        limit: int = 10_000,
    ) -> tuple[int, int]:
        """Return accumulated input/output tokens from assistant events."""
        if thread_id:
            events = self.events_by_thread(thread_id, limit=limit)
        elif session_id:
            events = self.events_by_session(session_id, limit=limit)
        else:
            return 0, 0

        input_tokens = 0
        output_tokens = 0
        for event in events:
            if event.get("type") != "assistant_message_emitted":
                continue
            payload = event.get("payload") or {}
            input_tokens += int(payload.get("input_tokens") or 0)
            output_tokens += int(payload.get("output_tokens") or 0)
        return input_tokens, output_tokens

    def _query_events(
        self,
        base_where: str,
        base_params: tuple[object, ...],
        *,
        limit: int,
        since_ts_ms: int | None,
        until_ts_ms: int | None,
    ) -> list[dict]:
        where = [base_where]
        params: list[object] = list(base_params)
        if since_ts_ms is not None:
            where.append("ts >= ?")
            params.append(int(since_ts_ms))
        if until_ts_ms is not None:
            where.append("ts <= ?")
            params.append(int(until_ts_ms))
        params.append(max(1, int(limit)))

        rows = self.conn.execute(
            "SELECT event_id, session_id, thread_id, run_id, step_id, type, "
            "payload_json, artifact_id, status, ts "
            f"FROM events WHERE {' AND '.join(where)} "
            "ORDER BY ts ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
