"""Local calendar tools — store events in the SQLite memory database.

Tool names deliberately avoid conflict with CalDAV calendar_tools.py:
  CalDAV: list_events, create_event, get_event, delete_event
  Local:  list_calendar_events, add_calendar_event, update_calendar_event, delete_calendar_event

Semantic scope resolution (backend-side, no LLM timezone math):
  today / tomorrow / this_week / today_remaining / date:YYYY-MM-DD
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from hushclaw.tools.base import tool, ToolResult

if TYPE_CHECKING:
    from hushclaw.memory.store import MemoryStore


# ── Shared scope → UTC range resolver ─────────────────────────────────────────

def _resolve_scope(
    scope: str,
    config=None,
    client_now: str = "",
) -> tuple[str, str] | None:
    """Return (from_utc, to_utc) for a semantic scope string, or None if unknown.

    All computation is done server-side in the user's configured calendar timezone.
    The LLM never needs to calculate UTC offsets.

    Supported scopes:
      today            — midnight-to-midnight today in user TZ
      tomorrow         — midnight-to-midnight tomorrow in user TZ
      this_week        — Monday 00:00 through Sunday 24:00 in user TZ
      today_remaining  — now (client_now or server now) through end of today in user TZ
      date:YYYY-MM-DD  — midnight-to-midnight on that specific date in user TZ
    """
    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None  # type: ignore[assignment, misc]

    tz_name = ""
    if config is not None:
        try:
            tz_name = config.calendar.timezone or ""
        except Exception:
            pass

    tz_obj = None
    if tz_name and ZoneInfo is not None:
        try:
            tz_obj = ZoneInfo(tz_name)
        except Exception:
            pass
    if tz_obj is None:
        tz_obj = datetime.now().astimezone().tzinfo or timezone.utc

    def _utc_str(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _day_window(d_local: datetime) -> tuple[str, str]:
        start = d_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return _utc_str(start), _utc_str(end)

    # Determine "now" in user TZ
    if client_now:
        try:
            now_utc = datetime.fromisoformat(client_now.replace("Z", "+00:00"))
            now_local = now_utc.astimezone(tz_obj)
        except Exception:
            now_local = datetime.now(tz_obj)
    else:
        now_local = datetime.now(tz_obj)

    scope = (scope or "").strip().lower()

    if scope == "today":
        return _day_window(now_local)

    if scope == "tomorrow":
        return _day_window(now_local + timedelta(days=1))

    if scope == "this_week":
        monday = now_local - timedelta(days=now_local.weekday())
        start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        return _utc_str(start), _utc_str(end)

    if scope == "today_remaining":
        start = now_local
        end_of_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return _utc_str(start), _utc_str(end_of_day)

    if scope.startswith("date:"):
        date_str = scope[5:].strip()
        try:
            from datetime import date as _date
            d = _date.fromisoformat(date_str)
            ref = datetime(d.year, d.month, d.day, tzinfo=tz_obj or timezone.utc)
            return _day_window(ref)
        except Exception:
            return None

    return None


def _fmt_event(e: dict) -> str:
    label = " [all-day]" if e.get("all_day") else ""
    loc = f" @ {e['location']}" if e.get("location") else ""
    return (
        f"  [{e['event_id']}] {e['title']}{label}{loc}\n"
        f"    {e['start_time']} → {e['end_time']}"
    )


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool(
    name="add_calendar_event",
    description=(
        "Create a calendar event stored locally. "
        "Use this whenever the user specifies a concrete date+time (e.g. '明天上午9:30', 'Friday at 3pm'). "
        "Prefer this over add_todo for any time-scheduled activity, appointment, or meeting. "
        "title (required): event name. "
        "start_time (required): ISO 8601 UTC datetime ending in Z, e.g. '2026-04-20T06:00:00Z'. "
        "Always convert the user's local time to UTC using the [TZ] hint in context before storing. "
        "end_time (required): ISO 8601 UTC datetime — estimate a reasonable duration if not given. "
        "description: optional details. "
        "location: optional location string. "
        "color: label color — indigo (default), rose, emerald, amber, sky, violet. "
        "all_day: true/false, default false. For all-day events use date-only format e.g. '2026-04-20'."
    ),
)
def add_calendar_event(
    title: str,
    start_time: str,
    end_time: str,
    description: str = "",
    location: str = "",
    color: str = "indigo",
    all_day: bool = False,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if not title or not title.strip():
        return ToolResult.error("title cannot be empty")
    if not start_time or not end_time:
        return ToolResult.error("start_time and end_time are required (ISO 8601 format)")
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    try:
        from datetime import datetime
        datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except ValueError as e:
        return ToolResult.error(f"Invalid datetime format: {e}. Use ISO 8601, e.g. '2026-04-20T14:00:00Z'.")
    valid_colors = {"indigo", "rose", "emerald", "amber", "sky", "violet"}
    if color not in valid_colors:
        color = "indigo"
    event = _memory_store.add_calendar_event(
        title=title,
        start_time=start_time,
        end_time=end_time,
        description=description,
        location=location,
        color=color,
        all_day=all_day,
    )
    return ToolResult.ok(
        f"Event created (id={event['event_id']}): {title!r} {start_time} → {end_time}"
    )


@tool(
    name="list_calendar_events",
    description=(
        "List calendar events in a time range. "
        "PREFERRED: use scope for common queries — backend computes UTC, no timezone math needed. "
        "scope values: \"today\", \"tomorrow\", \"this_week\", \"today_remaining\", \"date:YYYY-MM-DD\". "
        "Fallback: pass from_time and to_time as ISO 8601 UTC strings (e.g. '2026-04-21T07:00:00Z'). "
        "Omit all parameters to list all events."
    ),
    parallel_safe=True,
)
def list_calendar_events(
    scope: str = "",
    from_time: str = "",
    to_time: str = "",
    _memory_store: "MemoryStore | None" = None,
    _config=None,
    _client_now: str = "",
) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("Memory store not available")

    resolved_from = from_time or None
    resolved_to = to_time or None

    if scope:
        result = _resolve_scope(scope, _config, _client_now)
        if result is None:
            return ToolResult.error(
                f"Unknown scope {scope!r}. Use: today, tomorrow, this_week, today_remaining, date:YYYY-MM-DD"
            )
        resolved_from, resolved_to = result

    events = _memory_store.list_calendar_events(
        from_time=resolved_from,
        to_time=resolved_to,
    )
    if not events:
        return ToolResult.ok("No calendar events found.")
    lines = ["Calendar Events:"]
    for e in events:
        lines.append(_fmt_event(e))
    return ToolResult.ok("\n".join(lines))


@tool(
    name="get_day_agenda",
    description=(
        "Get a structured day agenda: events bucketed into morning/afternoon/evening, "
        "plus free slots within working hours. Better than list_calendar_events for "
        "'what's on today?' or 'do I have time for X?' queries. "
        "scope: \"today\" (default), \"tomorrow\", or \"date:YYYY-MM-DD\". "
        "working_hours: optional \"HH-HH\" string (default \"9-18\")."
    ),
    parallel_safe=True,
)
def get_day_agenda(
    scope: str = "today",
    working_hours: str = "9-18",
    _memory_store: "MemoryStore | None" = None,
    _config=None,
    _client_now: str = "",
) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("Memory store not available")

    range_result = _resolve_scope(scope, _config, _client_now)
    if range_result is None:
        return ToolResult.error(f"Unknown scope {scope!r}. Use: today, tomorrow, date:YYYY-MM-DD")
    from_utc, to_utc = range_result

    events = _memory_store.list_calendar_events(from_time=from_utc, to_time=to_utc)

    wh_start, wh_end = 9, 18
    try:
        parts = working_hours.split("-")
        wh_start, wh_end = int(parts[0]), int(parts[1])
    except Exception:
        pass

    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
        tz_name = (_config.calendar.timezone if _config else "") or ""
        tz_obj = ZoneInfo(tz_name) if tz_name else None
    except Exception:
        tz_obj = None

    def _to_local_hour(utc_str: str) -> float:
        try:
            dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
            if tz_obj:
                dt = dt.astimezone(tz_obj)
            return dt.hour + dt.minute / 60
        except Exception:
            return 0.0

    def _fmt_local(utc_str: str) -> str:
        try:
            dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
            if tz_obj:
                dt = dt.astimezone(tz_obj)
            return dt.strftime("%H:%M")
        except Exception:
            return utc_str

    morning, afternoon, evening, allday = [], [], [], []
    for e in events:
        if e.get("all_day"):
            allday.append(e)
            continue
        h = _to_local_hour(e["start_time"])
        if h < 12:
            morning.append(e)
        elif h < 17:
            afternoon.append(e)
        else:
            evening.append(e)

    lines = []
    if allday:
        lines.append(f"All-day ({len(allday)}):")
        for e in allday:
            lines.append(f"  [{e['event_id']}] {e['title']}")

    for label, bucket in [("Morning", morning), ("Afternoon", afternoon), ("Evening", evening)]:
        if bucket:
            lines.append(f"\n{label} ({len(bucket)}):")
            for e in bucket:
                loc = f" @ {e['location']}" if e.get("location") else ""
                lines.append(
                    f"  {_fmt_local(e['start_time'])}–{_fmt_local(e['end_time'])}  "
                    f"{e['title']}{loc}  [{e['event_id']}]"
                )

    if not lines:
        lines.append("No events scheduled.")

    # Free slots within working hours (best-effort)
    try:
        day_start_utc = datetime.fromisoformat(from_utc.replace("Z", "+00:00"))
        day_ref = day_start_utc.astimezone(tz_obj) if tz_obj else day_start_utc
        wh_start_dt = day_ref.replace(hour=wh_start, minute=0, second=0, microsecond=0)
        wh_end_dt = day_ref.replace(hour=wh_end, minute=0, second=0, microsecond=0)

        busy: list[tuple[datetime, datetime]] = []
        for e in events:
            if e.get("all_day"):
                continue
            try:
                s = datetime.fromisoformat(e["start_time"].replace("Z", "+00:00"))
                t = datetime.fromisoformat(e["end_time"].replace("Z", "+00:00"))
                if tz_obj:
                    s, t = s.astimezone(tz_obj), t.astimezone(tz_obj)
                busy.append((s, t))
            except Exception:
                continue

        busy.sort()
        merged: list[tuple[datetime, datetime]] = []
        for s, t in busy:
            if merged and s < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], t))
            else:
                merged.append((s, t))

        free_slots = []
        cursor = wh_start_dt
        for s, t in merged:
            s, t = max(s, wh_start_dt), min(t, wh_end_dt)
            if s > cursor:
                free_slots.append((cursor, s))
            if t > cursor:
                cursor = t
        if cursor < wh_end_dt:
            free_slots.append((cursor, wh_end_dt))

        if free_slots:
            lines.append(f"\nFree slots ({wh_start:02d}:00–{wh_end:02d}:00):")
            for s, t in free_slots:
                dur = int((t - s).total_seconds() // 60)
                lines.append(f"  {s.strftime('%H:%M')}–{t.strftime('%H:%M')}  ({dur} min)")
        else:
            lines.append(f"\nNo free slots in working hours ({wh_start:02d}:00–{wh_end:02d}:00).")
    except Exception:
        pass

    return ToolResult.ok("\n".join(lines))


@tool(
    name="find_free_slots",
    description=(
        "Find free time slots on a given day, filtered by minimum duration. "
        "scope: \"today\" (default), \"tomorrow\", or \"date:YYYY-MM-DD\". "
        "duration_minutes: minimum slot length to return (default 60). "
        "working_hours: optional \"HH-HH\" bounds, e.g. \"9-18\" (default 9-18)."
    ),
    parallel_safe=True,
)
def find_free_slots(
    scope: str = "today",
    duration_minutes: int = 60,
    working_hours: str = "9-18",
    _memory_store: "MemoryStore | None" = None,
    _config=None,
    _client_now: str = "",
) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("Memory store not available")

    range_result = _resolve_scope(scope, _config, _client_now)
    if range_result is None:
        return ToolResult.error(f"Unknown scope {scope!r}. Use: today, tomorrow, date:YYYY-MM-DD")
    from_utc, to_utc = range_result

    events = _memory_store.list_calendar_events(from_time=from_utc, to_time=to_utc)

    wh_start, wh_end = 9, 18
    try:
        parts = working_hours.split("-")
        wh_start, wh_end = int(parts[0]), int(parts[1])
    except Exception:
        pass

    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
        tz_name = (_config.calendar.timezone if _config else "") or ""
        tz_obj = ZoneInfo(tz_name) if tz_name else None
    except Exception:
        tz_obj = None

    try:
        day_ref_utc = datetime.fromisoformat(from_utc.replace("Z", "+00:00"))
        day_ref = day_ref_utc.astimezone(tz_obj) if tz_obj else day_ref_utc
        wh_start_dt = day_ref.replace(hour=wh_start, minute=0, second=0, microsecond=0)
        wh_end_dt = day_ref.replace(hour=wh_end, minute=0, second=0, microsecond=0)

        busy: list[tuple[datetime, datetime]] = []
        for e in events:
            if e.get("all_day"):
                continue
            try:
                s = datetime.fromisoformat(e["start_time"].replace("Z", "+00:00"))
                t = datetime.fromisoformat(e["end_time"].replace("Z", "+00:00"))
                if tz_obj:
                    s, t = s.astimezone(tz_obj), t.astimezone(tz_obj)
                busy.append((s, t))
            except Exception:
                continue

        busy.sort()
        merged: list[tuple[datetime, datetime]] = []
        for s, t in busy:
            if merged and s < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], t))
            else:
                merged.append((s, t))

        free: list[tuple[datetime, datetime]] = []
        cursor = wh_start_dt
        for s, t in merged:
            s, t = max(s, wh_start_dt), min(t, wh_end_dt)
            if s > cursor:
                free.append((cursor, s))
            if t > cursor:
                cursor = t
        if cursor < wh_end_dt:
            free.append((cursor, wh_end_dt))

        min_delta = timedelta(minutes=duration_minutes)
        qualifying = [(s, t) for s, t in free if t - s >= min_delta]

        if not qualifying:
            return ToolResult.ok(
                f"No free slots ≥ {duration_minutes} min found "
                f"within working hours {wh_start:02d}:00–{wh_end:02d}:00."
            )

        lines = [f"Free slots ≥ {duration_minutes} min:"]
        for s, t in qualifying:
            dur = int((t - s).total_seconds() // 60)
            lines.append(f"  {s.strftime('%H:%M')}–{t.strftime('%H:%M')}  ({dur} min)")
        return ToolResult.ok("\n".join(lines))

    except Exception as e:
        return ToolResult.error(f"find_free_slots failed: {e}")


@tool(
    name="check_time_conflicts",
    description=(
        "Check whether a proposed time slot conflicts with existing events. "
        "start_time and end_time: ISO 8601 UTC strings ending in Z. "
        "exclude_event_id: optionally exclude one event (e.g. the one being rescheduled). "
        "Returns conflicting events, or confirms no conflict."
    ),
    parallel_safe=True,
)
def check_time_conflicts(
    start_time: str,
    end_time: str,
    exclude_event_id: str = "",
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if not start_time or not end_time:
        return ToolResult.error("start_time and end_time are required")
    if _memory_store is None:
        return ToolResult.error("Memory store not available")

    events = _memory_store.list_calendar_events(from_time=start_time, to_time=end_time)
    conflicts = [
        e for e in events
        if not e.get("all_day") and e.get("event_id") != exclude_event_id
    ]
    if not conflicts:
        return ToolResult.ok(f"No conflicts for {start_time} → {end_time}.")
    lines = [f"Conflicts with {start_time} → {end_time}:"]
    for e in conflicts:
        lines.append(_fmt_event(e))
    return ToolResult.ok("\n".join(lines))


@tool(
    name="update_calendar_event",
    description=(
        "Update an existing calendar event. "
        "event_id (required): the ID from list_calendar_events. "
        "Provide any fields to update: title, start_time, end_time, description, location, color, all_day."
    ),
)
def update_calendar_event(
    event_id: str,
    title: str = "",
    start_time: str = "",
    end_time: str = "",
    description: str = "",
    location: str = "",
    color: str = "",
    all_day: bool | None = None,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if not event_id or not event_id.strip():
        return ToolResult.error("event_id is required")
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    updates: dict = {}
    if title:
        updates["title"] = title
    if start_time:
        updates["start_time"] = start_time
    if end_time:
        updates["end_time"] = end_time
    if description:
        updates["description"] = description
    if location:
        updates["location"] = location
    if color:
        updates["color"] = color
    if all_day is not None:
        updates["all_day"] = all_day
    if not updates:
        return ToolResult.error("No fields provided to update")
    updated = _memory_store.update_calendar_event(event_id, **updates)
    if not updated:
        return ToolResult.error(f"Event not found: {event_id!r}")
    return ToolResult.ok(f"Event updated: {updated['title']!r} ({event_id})")


@tool(
    name="delete_calendar_event",
    description=(
        "Delete a calendar event by ID. "
        "event_id (required): the ID from list_calendar_events."
    ),
)
def delete_calendar_event(
    event_id: str,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if not event_id or not event_id.strip():
        return ToolResult.error("event_id is required")
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    ok = _memory_store.delete_calendar_event(event_id)
    if not ok:
        return ToolResult.error(f"Event not found: {event_id!r}")
    return ToolResult.ok(f"Event deleted: {event_id}")
