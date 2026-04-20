"""server/calendar_mixin.py — WebSocket handlers for local calendar events.

Extracted as a mixin following the MemoryMixin/HttpMixin/ConfigMixin/ChatMixin pattern.
All handlers access the memory store via self._gateway.memory and send via ws.send.
"""
from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger(__name__)


class CalendarMixin:
    """Mixin for HushClawServer: local calendar event WebSocket handlers."""

    async def _handle_list_calendar_events(self, ws, data: dict) -> None:
        from_time = data.get("from_time") or None
        to_time = data.get("to_time") or None
        items = self._gateway.memory.list_calendar_events(
            from_time=from_time, to_time=to_time
        )
        await ws.send(json.dumps({"type": "calendar_events", "items": items}, default=str))

    async def _handle_create_calendar_event(self, ws, data: dict) -> None:
        mem = self._gateway.memory
        title = data.get("title", "").strip()
        if not title:
            await ws.send(json.dumps({"type": "error", "message": "title is required"}))
            return
        start_time = data.get("start_time", "")
        end_time = data.get("end_time", "")
        if not start_time or not end_time:
            await ws.send(json.dumps({"type": "error", "message": "start_time and end_time are required"}))
            return
        item = mem.add_calendar_event(
            title=title,
            start_time=start_time,
            end_time=end_time,
            description=data.get("description", ""),
            location=data.get("location", ""),
            color=data.get("color", "indigo"),
            all_day=bool(data.get("all_day", False)),
            attendees=data.get("attendees") or [],
        )
        await ws.send(json.dumps({"type": "calendar_event_created", "item": item}, default=str))

    async def _handle_update_calendar_event(self, ws, data: dict) -> None:
        mem = self._gateway.memory
        event_id = data.get("event_id", "").strip()
        if not event_id:
            await ws.send(json.dumps({"type": "error", "message": "event_id is required"}))
            return
        allowed = {"title", "start_time", "end_time", "description", "location", "color", "all_day", "attendees"}
        fields = {k: v for k, v in data.items() if k in allowed}
        item = mem.update_calendar_event(event_id, **fields)
        if item:
            await ws.send(json.dumps({"type": "calendar_event_updated", "item": item}, default=str))
        else:
            await ws.send(json.dumps({"type": "error", "message": f"Event not found: {event_id}"}))

    async def _handle_delete_calendar_event(self, ws, data: dict) -> None:
        event_id = data.get("event_id", "").strip()
        ok = self._gateway.memory.delete_calendar_event(event_id)
        await ws.send(json.dumps({"type": "calendar_event_deleted", "event_id": event_id, "ok": ok}))

    async def _handle_force_sync_caldav(self, ws, data: dict) -> None:
        """Trigger an immediate CalDAV → local SQLite pull and refresh the calendar."""
        log.info("[ws] force_sync_caldav: starting sync")
        _TIMEOUT = 60.0  # seconds

        error_msg: str | None = None
        count = 0
        last_sync = 0.0

        try:
            count = await asyncio.wait_for(
                self._connectors.force_caldav_sync(),
                timeout=_TIMEOUT,
            )
            last_sync = self._connectors.caldav_last_sync
            log.info("[ws] force_sync_caldav: done — %d events inserted/updated", count)
        except asyncio.TimeoutError:
            log.warning("[ws] force_sync_caldav: timed out after %.0fs", _TIMEOUT)
            error_msg = f"Sync timed out after {int(_TIMEOUT)}s"
        except Exception as exc:
            log.exception("[ws] force_sync_caldav: unexpected error: %s", exc)
            error_msg = str(exc) or "Unknown error"

        # Always refresh the client's event list, even on error
        try:
            items = self._gateway.memory.list_calendar_events()
        except Exception:
            items = []

        await ws.send(json.dumps({
            "type": "calendar_sync_done",
            "count": count,
            "last_sync": last_sync,
            "items": items,
            **({"error": error_msg} if error_msg else {}),
        }, default=str))
