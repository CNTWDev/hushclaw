"""CalDAVSyncService — background one-way sync from CalDAV to local SQLite.

Design: the AI layer never touches CalDAV directly; it reads/writes only the
local SQLite calendar_events table via local_calendar_tools. This service
pulls CalDAV events on a configurable interval and upserts them with
source='caldav'. Rows with source='local' are never touched by the sync.

Recurring events: expanded via the 'recurring-ical-events' library (installed
alongside 'caldav') for the configured date window. Each instance gets a
unique event_id = caldav:{uid}:{dtstart_iso}.

Dependencies: requires the 'caldav' library (not bundled — optional).
    pip install caldav   # also installs recurring-ical-events
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hushclaw.config.schema import CalendarConfig
    from hushclaw.memory.store import MemoryStore

log = logging.getLogger(__name__)

# Sync window: past N days and future N days relative to now.
_WINDOW_PAST_DAYS   = 30
_WINDOW_FUTURE_DAYS = 365


class CalDAVSyncService:
    """One-way pull: CalDAV → local SQLite calendar_events (source='caldav')."""

    def __init__(self, config: "CalendarConfig", memory: "MemoryStore") -> None:
        self._config = config
        self._memory = memory
        self._task: asyncio.Task | None = None
        self._last_sync: float = 0.0  # unix timestamp of last successful sync
        self._sync_lock = threading.Lock()  # prevents concurrent blocking syncs

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
            # All blocking network I/O runs in a thread — never blocks the event loop.
            count, seen_ids = await asyncio.to_thread(self._fetch_and_upsert, cfg)
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

    # ── Blocking helpers (run inside asyncio.to_thread) ───────────────────────

    def _fetch_and_upsert(self, cfg: "CalendarConfig") -> tuple[int, set[str]]:
        """Connect to CalDAV, fetch events for the date window, upsert into DB.

        Runs entirely in a worker thread — no asyncio calls allowed here.
        Returns (upserted_count, seen_event_ids).
        A threading.Lock prevents a lingering cancelled thread from running
        concurrently with the next sync.
        """
        if not self._sync_lock.acquire(blocking=False):
            log.warning("[caldav] sync already in progress (previous thread still running) — skipping")
            return 0, set()
        try:
            return self._do_fetch_and_upsert(cfg)
        finally:
            self._sync_lock.release()

    def _do_fetch_and_upsert(self, cfg: "CalendarConfig") -> tuple[int, set[str]]:
        import caldav  # type: ignore[import-untyped]

        client = caldav.DAVClient(
            url=cfg.url,
            username=cfg.username,
            password=cfg.password,
            timeout=45,  # per-request HTTP timeout (seconds)
        )
        principal = client.principal()
        calendars = principal.calendars()

        all_names = [getattr(c, "name", None) for c in calendars]
        log.info("[caldav] found %d calendar(s): %s", len(calendars), all_names)

        if cfg.calendar_name:
            calendars = [
                c for c in calendars
                if getattr(c, "name", None) == cfg.calendar_name
            ]
            log.info(
                "[caldav] after filter calendar_name=%r: %d calendar(s) match",
                cfg.calendar_name, len(calendars),
            )

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=_WINDOW_PAST_DAYS)
        window_end   = now + timedelta(days=_WINDOW_FUTURE_DAYS)

        count = 0
        seen_ids: set[str] = set()

        for calendar in calendars:
            cal_name = getattr(calendar, "name", "?")
            try:
                components = self._fetch_events(calendar, cal_name, window_start, window_end)
            except Exception:
                log.exception("[caldav] calendar %r: failed to fetch events — skipping", cal_name)
                continue

            for component in components:
                try:
                    vevents = self._expand_component(component, window_start, window_end)
                    for vevent in vevents:
                        changed, event_id = self._upsert_vevent(vevent)
                        if event_id:
                            seen_ids.add(event_id)
                        count += changed
                except Exception:
                    log.debug("[caldav] failed to process component", exc_info=True)

        return count, seen_ids

    def _fetch_events(self, calendar, cal_name: str, window_start: datetime, window_end: datetime) -> list:
        """Fetch event components from one calendar.

        Uses calendar.objects(load_objects=True) — a PROPFIND + multiget that
        is broadly compatible with CalDAV servers that don't properly support
        the calendar-query REPORT used by calendar.events().
        Falls back to calendar.events() if objects() is unavailable.
        Date filtering is applied client-side after the full fetch.
        """
        try:
            all_objects = calendar.objects(load_objects=True)
        except Exception as exc:
            log.warning(
                "[caldav] calendar %r: objects() failed (%s) — falling back to events()",
                cal_name, exc,
            )
            all_objects = calendar.events()

        log.info("[caldav] calendar %r: %d total object(s) fetched", cal_name, len(all_objects))

        # Client-side date filter: keep components that overlap [window_start, window_end].
        from datetime import date as _date
        filtered = []
        for component in all_objects:
            try:
                for sub in component.icalendar_component.subcomponents:
                    if getattr(sub, "name", None) != "VEVENT":
                        continue
                    dtstart = sub.get("DTSTART")
                    if not dtstart:
                        filtered.append(component)
                        break
                    start_val = dtstart.dt
                    if isinstance(start_val, _date) and not isinstance(start_val, datetime):
                        start_val = datetime(start_val.year, start_val.month, start_val.day, tzinfo=timezone.utc)
                    elif hasattr(start_val, "tzinfo") and start_val.tzinfo is None:
                        start_val = start_val.replace(tzinfo=timezone.utc)
                    if start_val <= window_end:
                        dtend = sub.get("DTEND")
                        end_val = dtend.dt if dtend else start_val
                        if isinstance(end_val, _date) and not isinstance(end_val, datetime):
                            end_val = datetime(end_val.year, end_val.month, end_val.day, tzinfo=timezone.utc)
                        elif hasattr(end_val, "tzinfo") and end_val.tzinfo is None:
                            end_val = end_val.replace(tzinfo=timezone.utc)
                        if end_val >= window_start or sub.get("RRULE"):
                            filtered.append(component)
                    break
            except Exception:
                filtered.append(component)  # keep on parse error

        log.info(
            "[caldav] calendar %r: %d component(s) in window after client-side filter",
            cal_name, len(filtered),
        )
        return filtered

    def _expand_component(self, component, window_start: datetime, window_end: datetime) -> list:
        """Return a flat list of VEVENT objects, expanding RRULE if present."""
        ical_obj = component.icalendar_component

        raw_vevents = [
            sub for sub in ical_obj.subcomponents
            if getattr(sub, "name", None) == "VEVENT"
        ]

        # Only bother expanding if there is at least one RRULE.
        if not any(ev.get("RRULE") for ev in raw_vevents):
            return raw_vevents

        try:
            import recurring_ical_events  # type: ignore[import-untyped]
            expanded = recurring_ical_events.of(ical_obj).between(window_start, window_end)
            log.debug(
                "[caldav] recurring event %r: %d instance(s) in window",
                str(raw_vevents[0].get("SUMMARY", "?")),
                len(expanded),
            )
            return expanded
        except Exception as exc:
            log.debug(
                "[caldav] recurring_ical_events expansion failed (%s) — using base VEVENT", exc
            )
            return raw_vevents

    def _upsert_vevent(self, vevent) -> tuple[int, str]:
        """Parse a VEVENT and upsert into local calendar_events.

        event_id format:
          - caldav:{uid}:{dtstart_iso}  — always, for both single and recurring instances.
            Using dtstart makes each recurring instance unique without needing RECURRENCE-ID.

        Returns (rowcount, event_id): rowcount is 1 if row was inserted/updated,
        0 if skipped (source='local' protection). event_id is the derived key,
        or '' on parse failure.
        """
        try:
            from datetime import date

            uid = str(vevent.get("UID", ""))
            if not uid:
                return 0, ""

            summary     = str(vevent.get("SUMMARY", "")     or "").strip() or "(no title)"
            description = str(vevent.get("DESCRIPTION", "") or "").strip()
            location    = str(vevent.get("LOCATION", "")    or "").strip()

            dtstart = vevent.get("DTSTART")
            dtend   = vevent.get("DTEND")
            if not dtstart or not dtend:
                return 0, ""

            start_val = dtstart.dt
            end_val   = dtend.dt

            all_day = isinstance(start_val, date) and not isinstance(start_val, datetime)

            if all_day:
                start_time = start_val.isoformat()
                end_time   = end_val.isoformat()
            else:
                if hasattr(start_val, "astimezone"):
                    start_time = start_val.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                    end_time   = end_val.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    start_time = start_val.isoformat()
                    end_time   = end_val.isoformat()

            # Unique key per instance: uid + dtstart covers recurring expansions.
            event_id = f"caldav:{uid}:{start_time}"

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
