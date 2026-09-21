"""Handlers for testing email (IMAP/SMTP) and CalDAV connections from the WebUI."""
from __future__ import annotations

import json
import asyncio

from hushclaw.util.caldav_auth import validate_caldav_password_auth
from hushclaw.util.mail_auth import imap_connection, smtp_connection, mail_error_message, resolve_test_account


async def handle_test_email(ws, data: dict, gateway) -> None:
    async def _send(msg: str, ok: bool | None = None) -> None:
        payload: dict = {"type": "test_integration_step", "target": "email", "message": msg}
        if ok is not None:
            payload["ok"] = ok
        await ws.send(json.dumps(payload))

    try:
        account = resolve_test_account(gateway.base_agent.config, data, 'email')
    except (ValueError, TypeError) as exc:
        await _send(str(exc), ok=False)
        await ws.send(json.dumps({'type':'test_integration_result', 'target':'email', 'ok':False, 'message':str(exc)}))
        return

    def test_imap():
        conn = imap_connection(account)
        try:
            typ, _ = conn.select(account.mailbox or 'INBOX', readonly=True)
            if typ != 'OK':
                raise ValueError('已登录，但无法访问所选邮箱文件夹；请检查文件夹名称和访问权限。')
        finally:
            try:
                conn.logout()
            except Exception:
                conn.shutdown()

    def test_smtp():
        with smtp_connection(account):
            pass  # Authentication only. Never send a test email.

    for protocol, host, port, check in (
        ('IMAP', account.imap_host, account.imap_port, test_imap),
        ('SMTP', account.smtp_host, account.smtp_port, test_smtp),
    ):
        await _send(f'正在测试 {protocol} {host}:{port}（TLS）…')
        try:
            await asyncio.to_thread(check)
            await _send(f'{protocol} 登录成功。', ok=True)
        except Exception as exc:
            message = mail_error_message(exc, host)
            await _send(f'{protocol}：{message}', ok=False)
            await ws.send(json.dumps({'type':'test_integration_result', 'target':'email', 'ok':False, 'message':message}))
            return

    await ws.send(json.dumps({"type": "test_integration_result", "target": "email", "ok": True,
                              "message": "Email configuration OK — IMAP and SMTP both connected successfully."}))


async def handle_test_calendar(ws, data: dict, gateway) -> None:
    cfg = gateway.base_agent.config

    index = data.get('account', 0)
    accounts = cfg.calendars
    base = accounts[index] if type(index) is int and 0 <= index < len(accounts) else None
    url = data.get('url', getattr(base, 'url', ''))

    async def _send(msg: str, ok: bool | None = None) -> None:
        payload: dict = {"type": "test_integration_step", "target": "calendar", "message": msg}
        if ok is not None:
            payload["ok"] = ok
        await ws.send(json.dumps(payload))

    try:
        validate_caldav_password_auth(url)
    except ValueError as exc:
        await _send(str(exc), ok=False)
        await ws.send(json.dumps({"type": "test_integration_result", "target": "calendar", "ok": False,
                                  "message": str(exc)}))
        return

    try:
        account = resolve_test_account(cfg, data, 'calendar')
        url, username, password, calendar_name = account.url, account.username, account.password, account.calendar_name
        if '://' not in url:
            url = 'https://' + url.lstrip('/')
    except (ValueError, TypeError) as exc:
        await ws.send(json.dumps({'type':'test_integration_result', 'target':'calendar', 'ok':False, 'message':str(exc)}))
        return

    try:
        import caldav  # noqa: F401
    except ImportError:
        await ws.send(json.dumps({"type": "test_integration_result", "target": "calendar", "ok": False,
                                  "message": "caldav package not installed. Run: pip install 'hushclaw[calendar]'"}))
        return

    await _send(f"Connecting to CalDAV {url} …")
    try:
        import caldav as _caldav
        def discover():
            client = _caldav.DAVClient(url=url, username=username, password=password, timeout=20)
            try:
                return [c.name for c in client.principal().calendars()]
            finally:
                client.close()
        names = await asyncio.to_thread(discover)
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
            await _send(f"Found {len(names)} calendar(s): {', '.join(names) or '(none)'}.")
    except Exception as exc:
        message = 'CalDAV 连接或授权失败，请核对 HTTPS 地址、用户名及日历专用凭据；邮箱凭据不一定适用于日历。'
        await _send(message, ok=False)
        await ws.send(json.dumps({"type": "test_integration_result", "target": "calendar", "ok": False,
                                  "message": message}))
        return

    await ws.send(json.dumps({"type": "test_integration_result", "target": "calendar", "ok": True,
                              "message": "CalDAV configuration OK — connected and listed calendars successfully."}))
