"""Handlers for testing email (IMAP/SMTP) and CalDAV connections from the WebUI."""
from __future__ import annotations

import imaplib
import json
import smtplib
import ssl
import types


async def handle_test_email(ws, data: dict, gateway) -> None:
    cfg = gateway.base_agent.config

    # Allow overriding fields from the UI payload (unsaved form values)
    imap_host = data.get("imap_host") or getattr(cfg.email, "imap_host", "")
    imap_port = int(data.get("imap_port") or getattr(cfg.email, "imap_port", 993))
    smtp_host = data.get("smtp_host") or getattr(cfg.email, "smtp_host", "")
    smtp_port = int(data.get("smtp_port") or getattr(cfg.email, "smtp_port", 587))
    username  = data.get("username")  or getattr(cfg.email, "username", "")
    password  = data.get("password")  or getattr(cfg.email, "password", "")

    async def _send(msg: str, ok: bool | None = None) -> None:
        payload: dict = {"type": "test_integration_step", "target": "email", "message": msg}
        if ok is not None:
            payload["ok"] = ok
        await ws.send(json.dumps(payload))

    # IMAP test
    await _send(f"Connecting to IMAP {imap_host}:{imap_port} …")
    try:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(imap_host, imap_port, ssl_context=ctx)
        conn.login(username, password)
        typ, data_resp = conn.select("INBOX")
        count = data_resp[0].decode() if data_resp and data_resp[0] else "?"
        conn.logout()
        await _send(f"IMAP OK — INBOX has {count} messages.")
    except Exception as exc:
        await _send(f"IMAP failed: {exc}", ok=False)
        await ws.send(json.dumps({"type": "test_integration_result", "target": "email", "ok": False,
                                  "message": "Email test failed (IMAP error)."}))
        return

    # SMTP test
    await _send(f"Connecting to SMTP {smtp_host}:{smtp_port} …")
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(username, password)
        await _send("SMTP OK — login successful.")
    except Exception as exc:
        await _send(f"SMTP failed: {exc}", ok=False)
        await ws.send(json.dumps({"type": "test_integration_result", "target": "email", "ok": False,
                                  "message": "Email test failed (SMTP error)."}))
        return

    await ws.send(json.dumps({"type": "test_integration_result", "target": "email", "ok": True,
                              "message": "Email configuration OK — IMAP and SMTP both connected successfully."}))


async def handle_test_calendar(ws, data: dict, gateway) -> None:
    cfg = gateway.base_agent.config

    url           = data.get("url")           or getattr(cfg.calendar, "url", "")
    username      = data.get("username")      or getattr(cfg.calendar, "username", "")
    password      = data.get("password")      or getattr(cfg.calendar, "password", "")
    calendar_name = data.get("calendar_name") or getattr(cfg.calendar, "calendar_name", "")

    async def _send(msg: str, ok: bool | None = None) -> None:
        payload: dict = {"type": "test_integration_step", "target": "calendar", "message": msg}
        if ok is not None:
            payload["ok"] = ok
        await ws.send(json.dumps(payload))

    try:
        import caldav  # noqa: F401
    except ImportError:
        await ws.send(json.dumps({"type": "test_integration_result", "target": "calendar", "ok": False,
                                  "message": "caldav package not installed. Run: pip install 'hushclaw[calendar]'"}))
        return

    await _send(f"Connecting to CalDAV {url} …")
    try:
        import caldav as _caldav
        client = _caldav.DAVClient(url=url, username=username, password=password)
        principal = client.principal()
        calendars = principal.calendars()
        names = [c.name for c in calendars]
        if calendar_name:
            matched = [n for n in names if n == calendar_name]
            if matched:
                await _send(f"Found calendar '{calendar_name}'.")
            else:
                await _send(f"Calendar '{calendar_name}' not found. Available: {', '.join(names) or '(none)'}", ok=False)
                await ws.send(json.dumps({"type": "test_integration_result", "target": "calendar", "ok": False,
                                          "message": f"Calendar '{calendar_name}' not found."}))
                return
        else:
            await _send(f"Found {len(calendars)} calendar(s): {', '.join(names) or '(none)'}.")
    except Exception as exc:
        await _send(f"Connection failed: {exc}", ok=False)
        await ws.send(json.dumps({"type": "test_integration_result", "target": "calendar", "ok": False,
                                  "message": f"CalDAV test failed: {exc}"}))
        return

    await ws.send(json.dumps({"type": "test_integration_result", "target": "calendar", "ok": True,
                              "message": "CalDAV configuration OK — connected and listed calendars successfully."}))
