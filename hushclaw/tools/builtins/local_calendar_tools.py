"""Local calendar tools — store events in the SQLite memory database.

Tool names deliberately avoid conflict with CalDAV calendar_tools.py:
  CalDAV: list_events, create_event, get_event, delete_event
  Local:  list_calendar_events, add_calendar_event, update_calendar_event, delete_calendar_event
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from hushclaw.tools.base import tool, ToolResult

if TYPE_CHECKING:
    from hushclaw.memory.store import MemoryStore


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
        datetime.fromisoformat(start_time)
        datetime.fromisoformat(end_time)
    except ValueError as e:
        return ToolResult.error(f"Invalid datetime format: {e}. Use ISO 8601, e.g. '2026-04-20T14:00:00'.")
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
        "List calendar events. "
        "from_time: ISO 8601 UTC datetime to filter from (optional), e.g. '2026-04-20T00:00:00Z'. "
        "to_time: ISO 8601 UTC datetime to filter until (optional). "
        "Convert the user's local time range to UTC using the [TZ] hint before filtering. "
        "Omit both to list all events."
    ),
    parallel_safe=True,
)
def list_calendar_events(
    from_time: str = "",
    to_time: str = "",
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    events = _memory_store.list_calendar_events(
        from_time=from_time or None,
        to_time=to_time or None,
    )
    if not events:
        return ToolResult.ok("No calendar events found.")
    lines = ["Calendar Events:"]
    for e in events:
        label = " [all-day]" if e["all_day"] else ""
        loc = f" @ {e['location']}" if e["location"] else ""
        lines.append(
            f"  [{e['event_id']}] {e['title']}{label}{loc}\n"
            f"    {e['start_time']} → {e['end_time']}"
        )
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
