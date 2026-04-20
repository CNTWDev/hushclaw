"""CalDAVSyncService — background one-way sync from CalDAV to local SQLite.

Design: the AI layer never touches CalDAV directly; it reads/writes only the
local SQLite calendar_events table via local_calendar_tools. This service
pulls CalDAV events on a configurable interval and upserts them with
source='caldav'. Rows with source='local' are never touched by the sync.

Fetch strategy:
  1. calendar-query REPORT (date_search) — single round trip, server-side
     time filtering, no stale-href 404s. Preferred for all servers that
     support it (Feishu included).
  2. PROPFIND Depth:1 + parallel GET — fallback if REPORT fails. Lists all
     hrefs then fetches each in a ThreadPoolExecutor (8 workers), followed
     by client-side date filtering.

Recurring events:
  - Standard RRULE: expanded via recurring-ical-events library
  - Feishu X-FEISHU-REPEAT: treated as recurring, included in every sync window

Dependencies: requires the 'caldav' library (not bundled — optional).
    pip install caldav   # also installs recurring-ical-events
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hushclaw.config.schema import CalendarConfig
    from hushclaw.memory.store import MemoryStore

log = logging.getLogger(__name__)

# Sync window relative to now.
_WINDOW_PAST_DAYS   = 365 * 5  # 5 years back — captures historical committee calendars
_WINDOW_FUTURE_DAYS = 365 * 2  # 2 years forward


class CalDAVSyncService:
    """One-way pull: CalDAV → local SQLite calendar_events (source='caldav')."""

    def __init__(self, config: "CalendarConfig", memory: "MemoryStore") -> None:
        self._config = config
        self._memory = memory
        self._task: asyncio.Task | None = None
        self._last_sync: float = 0.0
        self._sync_lock = threading.Lock()  # prevents concurrent blocking syncs
        self._stop_event = threading.Event()  # signals background thread to abort

    # ── Public API ────────────────────────────────────────────────────────────

    async def sync(self) -> int:
        """Pull CalDAV events and upsert into local DB. Returns inserted/updated count."""
        try:
            import caldav  # type: ignore[import-untyped]  # noqa: F401
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
            count = await asyncio.to_thread(self._fetch_and_upsert, cfg)
            import time as _t
            self._last_sync = _t.time()
            log.info("[caldav] sync complete: %d events inserted/updated", count)
            return count
        except Exception:
            log.exception("[caldav] sync error")
            return 0

    @property
    def last_sync(self) -> float:
        return self._last_sync

    async def start(self) -> None:
        self._stop_event.clear()
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="caldav-sync")
        log.info("[caldav] sync service started (interval=%d min)", self._config.sync_interval_minutes)

    async def stop(self) -> None:
        self._stop_event.set()  # signal any running thread to abort
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("[caldav] sync service stopped")

    # ── Internal loop ─────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while True:
            await self.sync()
            await asyncio.sleep(self._config.sync_interval_minutes * 60)

    # ── Blocking helpers (run inside asyncio.to_thread) ───────────────────────

    def _fetch_and_upsert(self, cfg: "CalendarConfig") -> int:
        # Abort immediately if stop was requested before we even try.
        if self._stop_event.is_set():
            log.info("[caldav] sync aborted — stop event set before lock acquired")
            return 0

        # Block until any concurrent sync finishes (up to 120s).
        log.info("[caldav] acquiring sync lock%s …",
                 " (another sync may be in progress — will wait up to 120s)" if self._sync_lock.locked() else "")
        acquired = self._sync_lock.acquire(blocking=True, timeout=120)
        if not acquired:
            log.warning("[caldav] timed out waiting for sync lock — skipping this run")
            return 0
        try:
            # Re-check after acquiring the lock (stop may have been set while waiting).
            if self._stop_event.is_set():
                log.info("[caldav] sync aborted — stop event set while waiting for lock")
                return 0
            log.info("[caldav] sync lock acquired")
            count, seen_ids = self._do_sync(cfg)
            # Prune inside the lock so a concurrent clear+resync cannot race with this prune.
            pruned = self._memory.prune_stale_caldav_events(seen_ids)
            if pruned:
                log.info("[caldav] pruned %d stale caldav events", pruned)
            return count
        finally:
            self._sync_lock.release()
            log.info("[caldav] sync lock released")

    def _do_sync(self, cfg: "CalendarConfig") -> tuple[int, set[str]]:
        import caldav  # type: ignore[import-untyped]

        # Normalise URL: add https:// if no scheme present.
        url = cfg.url.strip()
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
            log.info("[caldav] normalised url to %s", url)

        client = caldav.DAVClient(
            url=url,
            username=cfg.username,
            password=cfg.password,
            timeout=45,
        )
        principal = client.principal()
        calendars = principal.calendars()

        all_names = [getattr(c, "name", None) for c in calendars]
        log.info("[caldav] found %d calendar(s): %s", len(calendars), all_names)

        if cfg.calendar_name:
            calendars = [c for c in calendars if getattr(c, "name", None) == cfg.calendar_name]
            log.info("[caldav] after filter calendar_name=%r: %d calendar(s) match",
                     cfg.calendar_name, len(calendars))

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
                log.exception("[caldav] calendar %r: fetch failed — skipping", cal_name)
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
        """Fetch event objects for the given time window.

        Attempt 1 — calendar-query REPORT (date_search):
          Single HTTP round trip; server filters by time range; no stale 404s.
          Returns already-loaded CalendarObjectResource objects.

        Attempt 2 — PROPFIND Depth:1 + parallel GET (fallback):
          Lists all hrefs, fetches each via ThreadPoolExecutor (8 workers),
          then applies client-side date filtering.

        Both attempts are wrapped with a hard 60-second operation timeout using
        concurrent.futures so a slow/unresponsive server cannot hold the sync
        lock indefinitely.
        """
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
        _OP_TIMEOUT = 60  # seconds per network operation

        # Attempt 1: calendar-query REPORT — server-side time filtering.
        log.info("[caldav] calendar %r: attempting date_search (%s → %s)",
                 cal_name, window_start.date(), window_end.date())
        _ex1 = ThreadPoolExecutor(max_workers=1, thread_name_prefix="caldav-ds")
        try:
            _f1 = _ex1.submit(calendar.date_search, start=window_start, end=window_end)
            components = _f1.result(timeout=_OP_TIMEOUT)
            log.info("[caldav] calendar %r: %d component(s) via REPORT/date_search", cal_name, len(components))
            return components
        except FutureTimeoutError:
            log.warning("[caldav] calendar %r: date_search timed out after %ds — falling back to PROPFIND+GET",
                        cal_name, _OP_TIMEOUT)
        except Exception as exc:
            log.warning(
                "[caldav] calendar %r: REPORT/date_search failed (%s) — falling back to PROPFIND+GET",
                cal_name, exc, exc_info=True,
            )
        finally:
            _ex1.shutdown(wait=False)  # don't block on the abandoned thread

        # Attempt 2: PROPFIND Depth:1 to list hrefs, then parallel GET.
        log.info("[caldav] calendar %r: listing hrefs via PROPFIND", cal_name)
        _ex2 = ThreadPoolExecutor(max_workers=1, thread_name_prefix="caldav-pf")
        try:
            _f2 = _ex2.submit(calendar.objects, load_objects=False)
            hrefs = _f2.result(timeout=_OP_TIMEOUT)
        except FutureTimeoutError:
            log.warning("[caldav] calendar %r: PROPFIND listing timed out after %ds", cal_name, _OP_TIMEOUT)
            return []
        except Exception as exc:
            log.warning("[caldav] calendar %r: PROPFIND listing failed (%s)", cal_name, exc, exc_info=True)
            return []
        finally:
            _ex2.shutdown(wait=False)

        log.info("[caldav] calendar %r: %d href(s) found via PROPFIND", cal_name, len(hrefs))

        if not hrefs:
            return []

        def _load_one(obj):
            try:
                obj.load()
                return obj
            except Exception as exc_inner:
                # 404 = stale PROPFIND href (event deleted on server) — expected, noisy at WARNING
                is_404 = "404" in str(exc_inner) or "NotFound" in type(exc_inner).__name__
                if is_404:
                    log.debug("[caldav] 404 for %s (stale href — skipped)", getattr(obj, "url", "?"))
                else:
                    log.warning("[caldav] load failed for %s: %s", getattr(obj, "url", "?"), exc_inner)
                return None

        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="caldav-get") as pool:
            results = list(pool.map(_load_one, hrefs, timeout=120))

        loaded = [r for r in results if r is not None]
        n_404 = len(hrefs) - len(loaded)
        if n_404:
            log.info("[caldav] calendar %r: %d/%d objects loaded (%d stale/deleted hrefs skipped)",
                     cal_name, len(loaded), len(hrefs), n_404)
        else:
            log.info("[caldav] calendar %r: %d/%d objects loaded", cal_name, len(loaded), len(hrefs))

        filtered = self._filter_by_window(loaded, window_start, window_end)
        log.info("[caldav] calendar %r: %d component(s) in window (client filter)", cal_name, len(filtered))
        return filtered

    def _filter_by_window(self, components: list, window_start: datetime, window_end: datetime) -> list:
        """Keep components whose VEVENT overlaps [window_start, window_end].

        Recurring events (RRULE or X-FEISHU-REPEAT) are always kept since
        instances need to be expanded separately.
        """
        filtered = []
        sample_dates: list[str] = []  # for diagnostics

        for component in components:
            try:
                ical = component.icalendar_component
                # caldav 3.x: icalendar_component returns the VEVENT directly.
                # Older API: returns VCALENDAR with VEVENT as a subcomponent.
                if getattr(ical, "name", None) == "VEVENT":
                    vevents = [ical]
                else:
                    vevents = [s for s in ical.subcomponents if getattr(s, "name", None) == "VEVENT"]

                if not vevents:
                    filtered.append(component)  # keep on unknown structure
                    continue

                sub = vevents[0]
                # Always keep recurring events for expansion.
                if sub.get("RRULE") or sub.get("X-FEISHU-REPEAT"):
                    filtered.append(component)
                    continue
                dtstart = sub.get("DTSTART")
                if not dtstart:
                    filtered.append(component)
                    continue
                start_val = self._to_aware(dtstart.dt)
                if len(sample_dates) < 5:
                    sample_dates.append(str(start_val))
                if start_val > window_end:
                    continue  # too far in future
                dtend = sub.get("DTEND")
                end_val = self._to_aware(dtend.dt if dtend else dtstart.dt)
                if end_val >= window_start:
                    filtered.append(component)
            except Exception:
                filtered.append(component)  # keep on parse error

        if sample_dates:
            log.info("[caldav] sample event dates (first 5): %s", sample_dates)
        log.info("[caldav] window: %s → %s", window_start.date(), window_end.date())
        return filtered

    @staticmethod
    def _to_aware(val) -> datetime:
        """Normalise a date or naive datetime to a UTC-aware datetime."""
        if isinstance(val, _date) and not isinstance(val, datetime):
            return datetime(val.year, val.month, val.day, tzinfo=timezone.utc)
        if isinstance(val, datetime) and val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val  # type: ignore[return-value]

    def _expand_component(self, component, window_start: datetime, window_end: datetime) -> list:
        """Return flat list of VEVENT objects, expanding RRULE/X-FEISHU-REPEAT."""
        ical = component.icalendar_component
        # caldav 3.x: icalendar_component is the VEVENT itself.
        # Older API: it is the VCALENDAR wrapper.
        if getattr(ical, "name", None) == "VEVENT":
            raw_vevents = [ical]
            # recurring_ical_events needs the VCALENDAR — use icalendar_instance.
            ical_for_expand = component.icalendar_instance
        else:
            raw_vevents = [s for s in ical.subcomponents if getattr(s, "name", None) == "VEVENT"]
            ical_for_expand = ical

        has_rrule = any(ev.get("RRULE") or ev.get("X-FEISHU-REPEAT") for ev in raw_vevents)
        if not has_rrule:
            return raw_vevents

        try:
            import recurring_ical_events  # type: ignore[import-untyped]
            expanded = recurring_ical_events.of(ical_for_expand).between(window_start, window_end)
            log.debug("[caldav] recurring %r: %d instance(s) in window",
                      str(raw_vevents[0].get("SUMMARY", "?")), len(expanded))
            return expanded
        except Exception as exc:
            log.debug("[caldav] recurring expansion failed (%s) — using base VEVENT", exc)
            return raw_vevents

    def _upsert_vevent(self, vevent) -> tuple[int, str]:
        """Parse a VEVENT and upsert. Returns (rowcount, event_id)."""
        try:
            uid = str(vevent.get("UID", ""))
            if not uid:
                return 0, ""

            summary     = str(vevent.get("SUMMARY",     "") or "").strip() or "(no title)"
            description = str(vevent.get("DESCRIPTION", "") or "").strip()
            location    = str(vevent.get("LOCATION",    "") or "").strip()

            dtstart = vevent.get("DTSTART")
            dtend   = vevent.get("DTEND")
            if not dtstart or not dtend:
                return 0, ""

            start_val = dtstart.dt
            end_val   = dtend.dt
            all_day   = isinstance(start_val, _date) and not isinstance(start_val, datetime)

            if all_day:
                start_time = start_val.isoformat()
                end_time   = end_val.isoformat()
            else:
                start_time = self._to_aware(start_val).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                end_time   = self._to_aware(end_val).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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
