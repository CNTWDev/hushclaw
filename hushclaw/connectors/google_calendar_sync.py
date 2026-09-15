"""Pull Google Calendar into local calendar_events using OAuth, without CalDAV."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from hushclaw.app_connectors.google_calendar import GoogleCalendarClient
from hushclaw.config.schema import CalendarConfig
from hushclaw.connectors.caldav_sync import CalDAVSyncService


class GoogleCalendarSyncService(CalDAVSyncService):
    """Reuse the calendar scheduler/backoff; keep Google's snapshot isolated."""

    def __init__(self, config, memory, secrets):
        self._google_config = config
        self._secrets = secrets
        # A distinct persistence key; no user-provided URL receives OAuth tokens.
        super().__init__(CalendarConfig(
            enabled=True, url="https://www.googleapis.com/calendar/v3",
            username=config.access_token_ref, sync_interval_minutes=30,
        ), memory)

    def _ready_to_sync(self):
        return True  # Missing OAuth credentials are reported by the client.

    async def stop(self):
        await super().stop()
        # asyncio cancellation cannot terminate a worker thread. Wait until it
        # observes the stop event before a replacement service can start.
        await asyncio.to_thread(self._wait_for_worker)

    def _wait_for_worker(self):
        with self._sync_lock:
            pass

    def _fetch_and_upsert(self, cfg):
        with self._sync_lock:
            if self._stop_event.is_set():
                raise RuntimeError("Google Calendar sync stopped.")
            client = GoogleCalendarClient(self._google_config, self._secrets, self._stop_event)
            now = datetime.now(timezone.utc)
            start = (now - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
            end = (now + timedelta(days=730)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows = []
            for calendar in client.calendars():
                calendar_id = calendar["id"]
                for event in client.events(calendar_id, start, end):
                    if event.get("status") != "cancelled":
                        rows.append(self._event_row(calendar_id, event))
            if self._stop_event.is_set():
                raise RuntimeError("Google Calendar sync stopped.")
            # No local changes until every page from every calendar succeeded.
            return self._memory.replace_google_calendar_events(rows)

    @staticmethod
    def _event_row(calendar_id, event):
        start = event["start"]
        end = event["end"]
        all_day = "date" in start
        if all_day:
            start_time = date.fromisoformat(start["date"]).isoformat()
            end_time = date.fromisoformat(end["date"]).isoformat()
        else:
            def utc(value):
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    raise ValueError("Google Calendar returned a time without a UTC offset.")
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            start_time, end_time = utc(start["dateTime"]), utc(end["dateTime"])
        identity = json.dumps([calendar_id, event["id"]], ensure_ascii=False).encode()
        return {
            "event_id": "google:" + hashlib.sha256(identity).hexdigest(),
            "title": event.get("summary") or "(no title)",
            "start_time": start_time, "end_time": end_time, "all_day": all_day,
            "description": event.get("description") or "",
            "location": event.get("location") or "",
            "remote_uid": event["id"], "remote_calendar": calendar_id,
            "remote_etag": event.get("etag") or "",
            "attendees": [a["email"] for a in event.get("attendees", []) if a.get("email")],
        }
