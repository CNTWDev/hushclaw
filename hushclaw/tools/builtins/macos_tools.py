"""macOS AppleScript tools for Mail.app and Calendar.app — darwin only, zero deps.

These tools use the system's logged-in accounts automatically — no credentials needed.
They are only registered on macOS (sys.platform == "darwin").

Enable them by adding to tools.enabled:
    tools.enabled = [..., "macos_list_calendars", "macos_list_events",
                         "macos_create_calendar_event",
                         "macos_list_emails", "macos_send_email"]
"""
from __future__ import annotations

import json
import subprocess
import sys

from hushclaw.tools.base import tool, ToolResult


def _run_applescript(script: str, timeout: int = 15) -> str:
    """Execute an AppleScript and return stdout. Raises RuntimeError on failure."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"osascript exit {result.returncode}")
    return result.stdout.strip()


def _macos_only() -> ToolResult | None:
    """Return an error ToolResult if not running on macOS, else None."""
    if sys.platform != "darwin":
        return ToolResult.error("This tool is only available on macOS.")
    return None


@tool(description="List all calendar names in macOS Calendar.app.")
def macos_list_calendars() -> ToolResult:
    err = _macos_only()
    if err:
        return err
    script = """
tell application "Calendar"
    set output to {}
    repeat with c in calendars
        set end of output to name of c
    end repeat
    return output
end tell
"""
    try:
        raw = _run_applescript(script)
        # osascript returns a comma-separated list for AppleScript lists
        names = [n.strip() for n in raw.split(",") if n.strip()]
        return ToolResult.ok(json.dumps(names, ensure_ascii=False, indent=2))
    except Exception as e:
        return ToolResult.error(f"macos_list_calendars failed: {e}")


@tool(description=(
    "List calendar events in macOS Calendar.app within a date range. "
    "start_date and end_date format: 'YYYY-MM-DD'."
))
def macos_list_events(
    start_date: str,
    end_date: str,
    calendar_name: str = "",
) -> ToolResult:
    err = _macos_only()
    if err:
        return err

    if calendar_name:
        cal_clause = f'calendar "{calendar_name}"'
    else:
        cal_clause = "every calendar"

    # AppleScript date format: "MM/DD/YYYY HH:MM:SS"
    def _as_date(d: str) -> str:
        parts = d.split("-")
        if len(parts) == 3:
            y, m, day = parts
            return f"{m}/{day}/{y} 00:00:00"
        return d

    script = f"""
tell application "Calendar"
    set startDate to date "{_as_date(start_date)}"
    set endDate to date "{_as_date(end_date)}"
    set output to {{}}
    repeat with c in ({cal_clause})
        set evs to (every event of c whose start date >= startDate and start date <= endDate)
        repeat with e in evs
            set evInfo to (summary of e) & "|" & (start date of e as string) & "|" & (end date of e as string) & "|" & (name of c)
            set end of output to evInfo
        end repeat
    end repeat
    return output
end tell
"""
    try:
        raw = _run_applescript(script, timeout=30)
        events = []
        if raw:
            for line in raw.split(","):
                line = line.strip()
                if "|" in line:
                    parts = line.split("|")
                    events.append({
                        "summary":  parts[0].strip() if len(parts) > 0 else "",
                        "start":    parts[1].strip() if len(parts) > 1 else "",
                        "end":      parts[2].strip() if len(parts) > 2 else "",
                        "calendar": parts[3].strip() if len(parts) > 3 else "",
                    })
        return ToolResult.ok(json.dumps(events, ensure_ascii=False, indent=2))
    except Exception as e:
        return ToolResult.error(f"macos_list_events failed: {e}")


@tool(description=(
    "Create a new event in macOS Calendar.app. "
    "start_date and end_date format: 'YYYY-MM-DD HH:MM:SS'."
))
def macos_create_calendar_event(
    title: str,
    start_date: str,
    end_date: str,
    calendar_name: str = "",
    description: str = "",
    location: str = "",
) -> ToolResult:
    err = _macos_only()
    if err:
        return err

    def _esc(s: str) -> str:
        return s.replace('"', '\\"')

    # Default to first calendar if none specified
    if calendar_name:
        cal_ref = f'calendar "{_esc(calendar_name)}"'
    else:
        cal_ref = "first calendar"

    desc_line = f'        set description of newEvent to "{_esc(description)}"' if description else ""
    loc_line  = f'        set location of newEvent to "{_esc(location)}"'       if location  else ""

    script = f"""
tell application "Calendar"
    tell {cal_ref}
        set newEvent to make new event with properties {{summary:"{_esc(title)}", start date:date "{start_date}", end date:date "{end_date}"}}
{desc_line}
{loc_line}
    end tell
    reload calendars
end tell
return "ok"
"""
    try:
        _run_applescript(script, timeout=20)
        return ToolResult.ok(json.dumps({
            "ok": True,
            "title": title,
            "start": start_date,
            "end": end_date,
            "calendar": calendar_name or "(default)",
        }, ensure_ascii=False, indent=2))
    except Exception as e:
        return ToolResult.error(f"macos_create_calendar_event failed: {e}")


@tool(description=(
    "List recent emails in macOS Mail.app. "
    "mailbox_name examples: 'INBOX', 'Sent', 'Drafts'. "
    "account_name is optional; leave empty to search all accounts."
))
def macos_list_emails(
    mailbox_name: str = "INBOX",
    limit: int = 20,
    account_name: str = "",
) -> ToolResult:
    err = _macos_only()
    if err:
        return err

    def _esc(s: str) -> str:
        return s.replace('"', '\\"')

    if account_name:
        mailbox_ref = f'mailbox "{_esc(mailbox_name)}" of account "{_esc(account_name)}"'
    else:
        mailbox_ref = f'mailbox "{_esc(mailbox_name)}" of first account'

    script = f"""
tell application "Mail"
    set msgs to (messages of {mailbox_ref})
    set total to count of msgs
    if total > {limit} then set msgs to items 1 thru {limit} of msgs
    set output to {{}}
    repeat with m in msgs
        set info to (subject of m) & "|" & (sender of m) & "|" & (date received of m as string) & "|" & (read status of m as string)
        set end of output to info
    end repeat
    return output
end tell
"""
    try:
        raw = _run_applescript(script, timeout=20)
        emails = []
        if raw:
            for line in raw.split(","):
                line = line.strip()
                if "|" in line:
                    parts = line.split("|")
                    emails.append({
                        "subject": parts[0].strip() if len(parts) > 0 else "",
                        "from":    parts[1].strip() if len(parts) > 1 else "",
                        "date":    parts[2].strip() if len(parts) > 2 else "",
                        "read":    parts[3].strip() if len(parts) > 3 else "",
                    })
        return ToolResult.ok(json.dumps(emails, ensure_ascii=False, indent=2))
    except Exception as e:
        return ToolResult.error(f"macos_list_emails failed: {e}")


@tool(description="Send an email via macOS Mail.app using the system's logged-in mail account.")
def macos_send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
) -> ToolResult:
    err = _macos_only()
    if err:
        return err

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    cc_line = f'make new to recipient at end of cc recipients with properties {{address:"{_esc(cc)}"}}' if cc else ""

    script = f"""
tell application "Mail"
    set newMsg to make new outgoing message with properties {{subject:"{_esc(subject)}", content:"{_esc(body)}", visible:false}}
    tell newMsg
        make new to recipient at end of to recipients with properties {{address:"{_esc(to)}"}}
        {cc_line}
    end tell
    send newMsg
end tell
return "sent"
"""
    try:
        _run_applescript(script, timeout=20)
        return ToolResult.ok(f"Email sent to {to}" + (f", cc {cc}" if cc else ""))
    except Exception as e:
        return ToolResult.error(f"macos_send_email failed: {e}")
