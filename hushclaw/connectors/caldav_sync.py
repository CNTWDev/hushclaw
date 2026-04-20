"""CalDAVSyncService — background one-way sync from CalDAV to local SQLite.

Design: the AI layer never touches CalDAV directly; it reads/writes only the
local SQLite calendar_events table via local_calendar_tools. This service
pulls CalDAV events on a configurable interval and upserts them with
source='caldav'. Rows with source='local' are never touched by the sync.

Dependencies: requires the 'caldav' library (not bundled — optional).
    pip install caldav
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hushclaw.config.schema import CalendarConfig
    from hushclaw.memory.store import MemoryStore

log = logging.getLogger(__name__)


class CalDAVSyncService:
    """One-way pull: CalDAV → local SQLite calendar_events (source='caldav')."""

    def __init__(self, config: "CalendarConfig", memory: "MemoryStore") -> None:
        self._config = config
        self._memory = memory
        self._task: asyncio.Task | None = None
        self._last_sync: float = 0.0  # unix timestamp of last successful sync

    # ── Public API ────────────────────────────────────────────────────────────

    async def sync(self) -> int:
        """Pull CalDAV events and upsert into local DB. Returns count of rows actually inserted/updated."""
        try:
            import caldav  # type: ignore[import-untyped]
        except ImportError:
            log.warning(
                "[caldav] 'caldav' package not installed — sync skipped. "
                "Install with: pip install caldav"
            )
            return 0

        cfg = self._config
        if not cfg.url or not cfg.username:
            log.info("[caldav] sync skipped: calendar.url or calendar.username not configured")
            return 0

        log.info("[caldav] sync starting (url=%s, calendar=%r)", cfg.url, cfg.calendar_name or "*")

        try:
            client = caldav.DAVClient(
                url=cfg.url,
                username=cfg.username,
                password=cfg.password,
            )
            principal = client.principal()
            calendars = principal.calendars()
            if cfg.calendar_name:
                calendars = [
                    c for c in calendars
                    if getattr(c, "name", None) == cfg.calendar_name
                ]

            count = 0
            seen_ids: set[str] = set()
            for calendar in calendars:
                for component in calendar.events():
                    for vevent in component.icalendar_component.subcomponents:
                        if getattr(vevent, "name", None) != "VEVENT":
                            continue
                        changed, event_id = self._upsert_vevent(vevent)
                        if event_id:
                            seen_ids.add(event_id)
                        count += changed

            # Remove local caldav rows whose UID was not seen in this pull
            # (= events deleted on the CalDAV server)
            pruned = self._memory.prune_stale_caldav_events(seen_ids)
            if pruned:
                log.info("[caldav] pruned %d stale caldav events", pruned)

            import time as _t
            self._last_sync = _t.time()
            log.info("[caldav] sync complete: %d events inserted/updated", count)
            return count

        except Exception:
            log.exception("[caldav] sync error")
            return 0

    @property
    def last_sync(self) -> float:
        """Unix timestamp of the last successful sync (0 if never synced)."""
        return self._last_sync

    async def start(self) -> None:
        """Start the background sync loop (idempotent)."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="caldav-sync")
        log.info(
            "[caldav] sync service started (interval=%d min)",
            self._config.sync_interval_minutes,
        )

    async def stop(self) -> None:
        """Cancel the background loop and wait for it to finish."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("[caldav] sync service stopped")

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Sync, then sleep for sync_interval_minutes, repeat."""
        while True:
            await self.sync()
            await asyncio.sleep(self._config.sync_interval_minutes * 60)

    def _upsert_vevent(self, vevent) -> tuple[int, str]:
        """Parse a VEVENT and upsert into local calendar_events.

        Returns (rowcount, event_id): rowcount is 1 if row was inserted/updated,
        0 if skipped (source='local' protection). event_id is the derived key,
        or '' on parse failure.
        """
        try:
            from datetime import date, datetime, timezone

            uid = str(vevent.get("UID", ""))
            if not uid:
                return 0, ""
            event_id = f"caldav:{uid}"

            summary = str(vevent.get("SUMMARY", "") or "").strip() or "(no title)"
            description = str(vevent.get("DESCRIPTION", "") or "").strip()
            location = str(vevent.get("LOCATION", "") or "").strip()

            dtstart = vevent.get("DTSTART")
            dtend = vevent.get("DTEND")
            if not dtstart or not dtend:
                return 0, event_id

            start_val = dtstart.dt
            end_val = dtend.dt

            all_day = isinstance(start_val, date) and not isinstance(start_val, datetime)

            if all_day:
                start_time = start_val.isoformat()
                end_time = end_val.isoformat()
            else:
                if hasattr(start_val, "astimezone"):
                    start_time = start_val.astimezone(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    )
                    end_time = end_val.astimezone(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    )
                else:
                    start_time = start_val.isoformat()
                    end_time = end_val.isoformat()

            changed = self._memory.upsert_caldav_event(
                event_id=event_id,
                title=summary,
                description=description,
                location=location,
                start_time=start_time,
                end_time=end_time,
                all_day=all_day,
            )
            return changed, event_id

        except Exception:
            log.debug("[caldav] failed to parse VEVENT", exc_info=True)
            return 0, ""
