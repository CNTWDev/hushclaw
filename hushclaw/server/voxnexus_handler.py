"""Settings actions; no credentials are sent over WebSocket."""
from __future__ import annotations

import asyncio
import json
import urllib.parse
import webbrowser

from hushclaw.exceptions import ProviderError
from hushclaw.providers.voxnexus_auth import get_session, settings_config


def session_from_data(data, gateway):
    # Endpoint/client identity are deployment settings, never browser-controlled.
    config = settings_config(gateway.base_agent.config.provider)
    return get_session(config)


async def require_available(session):
    account = await session.request("/api/v1/me")
    if account.get("status") != "active":
        raise ProviderError("VoxNexus 账号不可用，请联系支持。", status_code=403)
    if float(account.get("available", 0)) <= 0:
        raise ProviderError("可用额度不足，请先充值。", status_code=402)
    return account


async def handle_voxnexus(ws, data, gateway):
    action = data.get("action", "status")
    result = {"type": "voxnexus_result", "action": action, "request_id": data.get("request_id"), "ok": True}
    try:
        session = session_from_data(data, gateway)
        if action == "login":
            url = await session.start_login()
            # Local deployment: open the actual OS browser, never an embedded webview.
            await asyncio.to_thread(webbrowser.open, url)
            result["authorize_url"] = url
        elif action == "logout":
            await session.logout()
        elif action == "status":
            pending = bool(session.login_task and not session.login_task.done())
            result.update(authed=await session.signed_in(), pending=pending, login_error=session.login_error)
        elif action == "account":
            result["account"] = await session.request("/api/v1/me")
        elif action == "models":
            await require_available(session)
            result["models"] = (await session.request("/v1/models")).get("data", [])
        elif action == "packages":
            result["packages"] = await session.request("/api/v1/packages")
        elif action == "topup":
            # Recheck server-side. No order is created when payments are disabled.
            packages = await session.request("/api/v1/packages")
            package_id = str(data.get("package_id") or "")
            if not packages.get("payments_enabled"):
                raise ProviderError("此网关暂未开启充值。")
            if not any(p.get("id") == package_id for p in packages.get("data", [])):
                raise ProviderError("充值套餐已变更，请刷新后重试。")
            # Use gateway's configured completion pages; poll independently of browser return.
            result["order"] = await session.request("/api/v1/topups", {"package_id": package_id})
            url = result["order"]["checkout_url"]
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme != "https" or parsed.hostname != "checkout.stripe.com" or parsed.username or parsed.password:
                raise ProviderError("网关返回的支付链接无效，请联系支持。")
            await asyncio.to_thread(webbrowser.open, url)
        elif action == "order":
            order_id = urllib.parse.quote(str(data.get("order_id") or ""), safe="")
            if not order_id:
                raise ProviderError("缺少订单编号。")
            result["order"] = await session.request("/api/v1/topups/" + order_id)
        else:
            raise ProviderError("未知 VoxNexus 操作。")
    except ProviderError as exc:
        result.update(ok=False, error=str(exc), status=exc.status_code)
    except Exception:
        # Never echo OAuth tokens, authorization codes, or payment payloads in errors/logs.
        result.update(ok=False, error="VoxNexus 请求失败，请检查网络及系统钥匙串后重试。")
    await ws.send(json.dumps(result))
