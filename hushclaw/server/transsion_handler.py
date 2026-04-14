"""Transsion / TEX AI Router auth flow handlers — extracted from server_impl.py.

Handles three WebSocket messages:
  transsion_send_code  — step 1: send OTP email
  transsion_login      — step 2: exchange OTP for credentials
  transsion_quota      — query remaining token quota
"""
from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("hushclaw.server.transsion")


async def handle_send_code(ws, data: dict) -> None:
    """Send OTP verification code to the user's email (step 1 of Transsion auth)."""
    from hushclaw.providers.transsion import send_email_code

    email = (data.get("email") or "").strip()
    if not email:
        await ws.send(json.dumps({"type": "error", "message": "email is required"}))
        return

    log.info("transsion_send_code: requesting OTP for email=%s", email)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="hushclaw-transsion"),
            send_email_code,
            email,
        )
        log.info("transsion_send_code: OTP dispatched for email=%s", email)
        await ws.send(json.dumps({
            "type": "transsion_code_sent",
            "email": email,
        }))
    except Exception as e:
        log.exception("transsion_send_code failed for email=%s", email)
        await ws.send(json.dumps({"type": "error", "message": str(e)}))


async def handle_login(ws, data: dict) -> None:
    """Log in with email + OTP, acquire credentials (step 2 of Transsion auth)."""
    import functools
    from hushclaw.providers.transsion import acquire_credentials

    email = (data.get("email") or "").strip()
    code = (data.get("code") or "").strip()
    if not email or not code:
        await ws.send(json.dumps({"type": "error", "message": "email and code are required"}))
        return

    log.info("transsion_login: starting acquire_credentials for email=%s", email)
    loop = asyncio.get_event_loop()
    try:
        creds: dict = await loop.run_in_executor(
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="hushclaw-transsion"),
            functools.partial(acquire_credentials, email, code),
        )
    except Exception as e:
        log.exception("transsion_login failed for email=%s", email)
        await ws.send(json.dumps({"type": "error", "message": str(e)}))
        return

    base_url_v1 = creds["base_url"].rstrip("/") + "/v1"
    await ws.send(json.dumps({
        "type": "transsion_authed",
        "display_name": creds["display_name"],
        "email": creds["email"],
        "access_token": creds["access_token"],
        "api_key": creds["api_key"],
        "models": creds["models"],
        "quota_remain": creds["quota_remain"],
        "base_url": base_url_v1,
    }))
    log.info(
        "transsion_login: credentials issued for %s (%s)  models=%d  quota=%s "
        "(persist on user Save)",
        creds["display_name"], email, len(creds["models"]), creds["quota_remain"],
    )


async def handle_quota(ws, data: dict, gateway) -> None:
    """Query remaining token quota for the currently authenticated Transsion account."""
    import functools
    from hushclaw.providers.transsion import get_quota_remaining

    cfg = gateway.base_agent.config
    access_token = cfg.transsion.access_token
    api_key = cfg.provider.api_key

    if not access_token or not api_key:
        await ws.send(json.dumps({
            "type": "transsion_quota_result",
            "ok": False,
            "error": "Not authenticated — please log in first.",
        }))
        return

    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="hushclaw-transsion"),
            functools.partial(get_quota_remaining, access_token, api_key),
        )
        await ws.send(json.dumps({"type": "transsion_quota_result", "ok": True, "info": info}))
    except Exception as e:
        log.exception("transsion_quota failed")
        await ws.send(json.dumps({"type": "transsion_quota_result", "ok": False, "error": str(e)}))
