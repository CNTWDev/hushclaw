"""CalDAV calendar tools — requires optional 'caldav' package.

Install: pip install hushclaw[calendar]  (or pip install caldav>=1.3)

Enable these tools by adding them to tools.enabled in your config:
    tools.enabled = [..., "list_calendars", "list_events", "get_event",
                         "create_event", "delete_event"]

Also configure the [calendar] section:
    [calendar]
    enabled = true
    url = "https://www.google.com/calendar/dav"
    username = "you@gmail.com"
    password = "app-password-here"
    calendar_name = ""   # empty = all calendars
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from hushclaw.tools.base import tool, ToolResult

try:
    import caldav
    _CALDAV_AVAILABLE = True
except ImportError:
    _CALDAV_AVAILABLE = False


def _caldav_client(cfg):
    """Return an authenticated CalDAV client and principal."""
    client = caldav.DAVClient(
        url=cfg.calendar.url,
        username=cfg.calendar.username,
        password=cfg.calendar.password,
    )
    principal = client.principal()
    return principal


def _get_calendars(principal, calendar_name: str):
    """Return list of calendars; filtered by name if calendar_name is non-empty."""
    calendars = principal.calendars()
    if calendar_name:
        calendars = [c for c in calendars if c.name == calendar_name]
    return calendars


def _parse_dt(value: str) -> datetime:
    """Parse an ISO 8601 datetime string."""
    return datetime.fromisoformat(value)


def _event_to_dict(event) -> dict:
    """Convert a caldav Event to a plain dict."""
    try:
        comp = event.icalendar_component
        return {
            "uid":         str(comp.get("UID", "")),
            "summary":     str(comp.get("SUMMARY", "(no title)")),
            "start":       str(comp.get("DTSTART").dt) if comp.get("DTSTART") else "",
            "end":         str(comp.get("DTEND").dt) if comp.get("DTEND") else "",
            "description": str(comp.get("DESCRIPTION", "")),
            "location":    str(comp.get("LOCATION", "")),
            "url":         event.url if hasattr(event, "url") else "",
        }
    except Exception as e:
        return {"error": str(e)}


@tool(description="List all available CalDAV calendars.")
def list_calendars(_config=None) -> ToolResult:
    if not (_config and _config.calendar.enabled):
        return ToolResult.error("Calendar not configured. Set [calendar] enabled = true in config.")
    if not _CALDAV_AVAILABLE:
        return ToolResult.error("caldav package not installed. Run: pip install caldav>=1.3")
    try:
        principal = _caldav_client(_config)
        calendars = principal.calendars()
        result = [{"name": c.name, "url": str(c.url)} for c in calendars]
        return ToolResult.ok(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        return ToolResult.error(f"list_calendars failed: {e}")


@tool(description=(
    "List calendar events within a date/time range. "
    "start and end must be ISO 8601 strings, e.g. '2026-03-01T00:00:00'."
))
def list_events(
    start: str,
    end: str,
    calendar_name: str = "",
    _config=None,
) -> ToolResult:
    if not (_config and _config.calendar.enabled):
        return ToolResult.error("Calendar not configured. Set [calendar] enabled = true in config.")
    if not _CALDAV_AVAILABLE:
        return ToolResult.error("caldav package not installed. Run: pip install caldav>=1.3")
    try:
        cal_name = calendar_name or _config.calendar.calendar_name
        principal = _caldav_client(_config)
        calendars = _get_calendars(principal, cal_name)
        start_dt = _parse_dt(start)
        end_dt = _parse_dt(end)
        results = []
        for cal in calendars:
            events = cal.date_search(start=start_dt, end=end_dt, expand=True)
            for ev in events:
                d = _event_to_dict(ev)
                d["calendar"] = cal.name
                results.append(d)
        results.sort(key=lambda x: x.get("start", ""))
        return ToolResult.ok(json.dumps(results, ensure_ascii=False, indent=2))
    except Exception as e:
        return ToolResult.error(f"list_events failed: {e}")


@tool(description="Get details of a specific calendar event by its UID.")
def get_event(event_id: str, calendar_name: str = "", _config=None) -> ToolResult:
    if not (_config and _config.calendar.enabled):
        return ToolResult.error("Calendar not configured. Set [calendar] enabled = true in config.")
    if not _CALDAV_AVAILABLE:
        return ToolResult.error("caldav package not installed. Run: pip install caldav>=1.3")
    try:
        cal_name = calendar_name or _config.calendar.calendar_name
        principal = _caldav_client(_config)
        calendars = _get_calendars(principal, cal_name)
        for cal in calendars:
            try:
                events = cal.events()
                for ev in events:
                    comp = ev.icalendar_component
                    if str(comp.get("UID", "")) == event_id:
                        d = _event_to_dict(ev)
                        d["calendar"] = cal.name
                        return ToolResult.ok(json.dumps(d, ensure_ascii=False, indent=2))
            except Exception:
                continue
        return ToolResult.error(f"Event uid={event_id} not found")
    except Exception as e:
        return ToolResult.error(f"get_event failed: {e}")


@tool(description=(
    "Create a new calendar event. "
    "start and end must be ISO 8601 strings, e.g. '2026-03-20T10:00:00'."
))
def create_event(
    title: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
    calendar_name: str = "",
    _config=None,
) -> ToolResult:
    if not (_config and _config.calendar.enabled):
        return ToolResult.error("Calendar not configured. Set [calendar] enabled = true in config.")
    if not _CALDAV_AVAILABLE:
        return ToolResult.error("caldav package not installed. Run: pip install caldav>=1.3")
    try:
        cal_name = calendar_name or _config.calendar.calendar_name
        principal = _caldav_client(_config)
        calendars = _get_calendars(principal, cal_name)
        if not calendars:
            return ToolResult.error(
                f"No calendar found{f' named {cal_name!r}' if cal_name else ''}. "
                "Use list_calendars to see available calendars."
            )
        target_cal = calendars[0]

        start_dt = _parse_dt(start)
        end_dt = _parse_dt(end)
        uid = str(uuid.uuid4())

        def _fmt(dt: datetime) -> str:
            return dt.strftime("%Y%m%dT%H%M%S")

        ical_lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//HushClaw//CalDAV//EN",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"SUMMARY:{title}",
            f"DTSTART:{_fmt(start_dt)}",
            f"DTEND:{_fmt(end_dt)}",
        ]
        if description:
            ical_lines.append(f"DESCRIPTION:{description}")
        if location:
            ical_lines.append(f"LOCATION:{location}")
        ical_lines += ["END:VEVENT", "END:VCALENDAR"]
        ical_str = "\r\n".join(ical_lines)

        target_cal.save_event(ical_str)
        return ToolResult.ok(json.dumps({
            "ok": True,
            "uid": uid,
            "title": title,
            "start": start,
            "end": end,
            "calendar": target_cal.name,
        }, ensure_ascii=False, indent=2))
    except Exception as e:
        return ToolResult.error(f"create_event failed: {e}")


@tool(description="Delete a calendar event by its UID.")
def delete_event(event_id: str, calendar_name: str = "", _config=None) -> ToolResult:
    if not (_config and _config.calendar.enabled):
        return ToolResult.error("Calendar not configured. Set [calendar] enabled = true in config.")
    if not _CALDAV_AVAILABLE:
        return ToolResult.error("caldav package not installed. Run: pip install caldav>=1.3")
    try:
        cal_name = calendar_name or _config.calendar.calendar_name
        principal = _caldav_client(_config)
        calendars = _get_calendars(principal, cal_name)
        for cal in calendars:
            try:
                events = cal.events()
                for ev in events:
                    comp = ev.icalendar_component
                    if str(comp.get("UID", "")) == event_id:
                        ev.delete()
                        return ToolResult.ok(f"Event uid={event_id} deleted from {cal.name}")
            except Exception:
                continue
        return ToolResult.error(f"Event uid={event_id} not found")
    except Exception as e:
        return ToolResult.error(f"delete_event failed: {e}")
