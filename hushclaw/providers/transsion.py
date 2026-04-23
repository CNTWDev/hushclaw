"""Transsion / TEX AI Router provider.

Two-step auth flow:
  1. send_email_code(email)  — sends OTP to the user's email
  2. acquire_credentials(email, code)  — logs in, exchanges accessToken for sk-xxx + baseUrl

After credential acquisition, LLM calls use the standard OpenAI-compatible
chat/completions API at airouter.aibotplatform.com, handled by OpenAIRawProvider.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from hushclaw.exceptions import ProviderError
from hushclaw.providers.base import LLMResponse, Message
from hushclaw.providers.openai_raw import OpenAIRawProvider
from hushclaw.util.logging import get_logger
from hushclaw.util.ssl_context import make_ssl_context

log = get_logger("providers.transsion")

_AUTH_BASE = os.environ.get(
    "HUSHCLAW_TRANSSION_AUTH_BASE",
    "https://bus-ie.aibotplatform.com",
).rstrip("/")
_APP_ID = os.environ.get("HUSHCLAW_TRANSSION_APP_ID", "jwouyypn")
# AcquireAPICredentials requires extra metadata (businessName, clientID). Backend does not
# validate clientID; fixed opaque id (64-char hex) — no env, stable across installs.
_ACQUIRE_APP_ID = os.environ.get("HUSHCLAW_TRANSSION_ACQUIRE_APP_ID") or _APP_ID
_ACQUIRE_BUSINESS_NAME = "hushclaw"
_ACQUIRE_CLIENT_ID = "c0c1086f7cefbe5b2ce082ba8720dcac04b3559b509d3bc65972bbc1b036b2f0"  # 64 hex, opaque
# airouter is the AI runtime endpoint; bus-ie is the control-plane (auth/credentials).
# These are distinct services — never normalise one into the other.
_DEFAULT_ROUTER_BASE = "https://airouter.aibotplatform.com"


def _normalize_router_base(base_url: str) -> str:
    """Return the router base URL, appending /v1 when the path is empty."""
    url = (base_url or "").strip()
    if not url:
        return f"{_DEFAULT_ROUTER_BASE}/v1"
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(url)
    if (parsed.path or "").rstrip("/") in ("", "/"):
        parsed = parsed._replace(path="/v1")
        return urlunparse(parsed).rstrip("/")
    return url


def _make_metadata(request_id: str | None = None) -> dict:
    rid = request_id or f"req_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{uuid.uuid4().hex[:10]}"
    return {
        "appID": _APP_ID,
        "requestID": rid,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
    }


def _make_credentials_metadata() -> dict:
    """Metadata for POST .../oneapi/api-credentials/info (TEX AI Router integration guide)."""
    rid = f"req_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{uuid.uuid4().hex[:10]}"
    return {
        "requestID": rid,
        "appID": _ACQUIRE_APP_ID,
        "businessName": _ACQUIRE_BUSINESS_NAME,
        "clientID": _ACQUIRE_CLIENT_ID,
    }


def _post_json(
    url: str,
    payload: dict,
    headers: dict | None = None,
    timeout: int = 30,
    *,
    op: str = "transsion",
) -> dict:
    data = json.dumps(payload).encode()
    req_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        req_headers.update(headers)
    log.info("[transsion] %s → POST %s", op, url)
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=make_ssl_context()) as resp:
            raw = resp.read()
            body = json.loads(raw)
    except urllib.error.HTTPError as e:
        body_str = e.read().decode("utf-8", errors="replace")
        log.warning(
            "[transsion] %s HTTP %s — response (first 1200 chars): %s",
            op,
            e.code,
            body_str[:1200],
        )
        raise ProviderError(f"Transsion auth HTTP {e.code}: {body_str[:400]}") from e
    except Exception as e:
        log.warning("[transsion] %s request failed: %s", op, e)
        raise ProviderError(f"Transsion auth request failed: {e}") from e

    meta = body.get("metadata", {}) if isinstance(body, dict) else {}
    code = meta.get("code", 0)
    if code != 200:
        log.warning(
            "[transsion] %s metadata.code=%s requestID=%s debugMessage=%r elapsed=%s",
            op,
            code,
            meta.get("requestID"),
            meta.get("debugMessage"),
            meta.get("elapsed"),
        )
        debug = meta.get("debugMessage", "")
        raise ProviderError(f"Transsion API error (code={code}): {debug or body}")
    pl = body.get("payload", {})
    log.info(
        "[transsion] %s OK requestID=%s payload_keys=%s",
        op,
        meta.get("requestID"),
        list(pl.keys()) if isinstance(pl, dict) else type(pl).__name__,
    )
    return pl


def send_email_code(email: str, timeout: int = 30) -> None:
    """Send OTP verification code to *email*.

    Raises ProviderError on failure.
    """
    url = f"{_AUTH_BASE}/assistant/vendor-api/v1/auth/send-email-code"
    payload = {
        "metadata": _make_metadata(),
        "payload": {"email": email},
    }
    log.info("[transsion] send_email_code: email=%s appID=%s", email, _APP_ID)
    result = _post_json(url, payload, timeout=timeout, op="send_email_code")
    if not result.get("success"):
        raise ProviderError(f"Failed to send verification code to {email!r}")
    expire = result.get("expireSeconds", 300)
    log.info("[transsion] code sent to %s, expires in %ds", email, expire)


def acquire_credentials(email: str, code: str, timeout: int = 30) -> dict:
    """Log in with *email* + OTP *code*, then exchange accessToken for API credentials.

    Returns a dict with:
      - api_key: str       — sk-xxx, used as Bearer token for LLM calls
      - base_url: str      — router base URL (e.g. https://airouter.aibotplatform.com)
      - access_token: str  — JWT for future credential refresh
      - display_name: str  — user's display name
      - email: str
      - models: list[str]  — chat/completions-compatible model IDs
      - quota_remain: str  — remaining quota string
    """
    # Step 1: email-code login
    login_url = f"{_AUTH_BASE}/assistant/vendor-api/v1/auth/email-code-login"
    login_payload = {
        "metadata": _make_metadata(),
        "payload": {"email": email, "emailCode": code},
    }
    log.info("[transsion] acquire_credentials: email_code_login email=%s appID=%s", email, _APP_ID)
    login_result = _post_json(login_url, login_payload, timeout=timeout, op="email_code_login")
    access_token: str = login_result.get("accessToken", "")
    display_name: str = login_result.get("displayName", "")
    if not access_token:
        log.warning(
            "[transsion] email_code_login returned no accessToken; payload_keys=%s",
            list(login_result.keys()) if isinstance(login_result, dict) else login_result,
        )
        raise ProviderError("Transsion login succeeded but no accessToken returned")
    log.info(
        "[transsion] email_code_login OK accessToken_len=%d displayName=%r",
        len(access_token),
        display_name or "",
    )

    # Step 2: acquire API credentials using accessToken
    creds_url = f"{_AUTH_BASE}/assistant/vendor-api/v1/oneapi/api-credentials/info"
    cred_meta = _make_credentials_metadata()
    creds_payload = {
        "metadata": cred_meta,
        "payload": {},
    }
    creds_headers = {
        "Authorization": f"pf-sso {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    log.info(
        "[transsion] api_credentials/info metadata appID=%s businessName=%s clientID=%s requestID=%s",
        cred_meta.get("appID"),
        cred_meta.get("businessName"),
        cred_meta.get("clientID"),
        cred_meta.get("requestID"),
    )
    creds_result = _post_json(
        creds_url, creds_payload, headers=creds_headers, timeout=timeout, op="api_credentials_info"
    )

    token_info: dict = creds_result.get("tokenInfo", {})
    api_key: str = token_info.get("key", "")
    remain_quota = str(token_info.get("remainQuota", ""))
    base_url: str = creds_result.get("baseUrl", _DEFAULT_ROUTER_BASE).rstrip("/")
    raw_models: list[dict] = creds_result.get("models", [])

    if not api_key:
        log.warning(
            "[transsion] api_credentials_info: missing tokenInfo.key; tokenInfo_keys=%s top_keys=%s",
            list(token_info.keys()) if isinstance(token_info, dict) else type(token_info).__name__,
            list(creds_result.keys()) if isinstance(creds_result, dict) else type(creds_result).__name__,
        )
        raise ProviderError("Transsion credential acquisition returned no API key")

    # Filter to models that support chat/completions (exclude images/generations-only models)
    chat_models = [
        m["model"] for m in raw_models
        if isinstance(m, dict)
        and "chat/completions" in (m.get("supportedAPIs") or [])
    ]
    if not chat_models:
        # Fallback: include everything if filter produces nothing
        chat_models = [m["model"] for m in raw_models if isinstance(m, dict) and "model" in m]

    user_info: dict = creds_result.get("userInfo", {})
    if not display_name:
        display_name = user_info.get("displayName", user_info.get("username", email))

    log.info(
        "[transsion] credentials acquired: key=%s...%s  base_url=%s  models=%d  quota=%s",
        api_key[:4], api_key[-4:], base_url, len(chat_models), remain_quota,
    )
    return {
        "api_key": api_key,
        "base_url": base_url,
        "access_token": access_token,
        "display_name": display_name,
        "email": email,
        "models": chat_models,
        "quota_remain": remain_quota,
    }


def get_models_from_credentials(access_token: str, timeout: int = 30) -> list[str]:
    """Fetch the model list from the Transsion control plane using a live access token.

    This calls the same /oneapi/api-credentials/info endpoint used by
    acquire_credentials so we always get the canonical, up-to-date list
    without requiring a full re-login.  Raises ProviderError on failure.
    """
    if not access_token:
        raise ProviderError("Transsion list_models: access_token is empty")
    creds_url = f"{_AUTH_BASE}/assistant/vendor-api/v1/oneapi/api-credentials/info"
    cred_meta = _make_credentials_metadata()
    creds_payload = {"metadata": cred_meta, "payload": {}}
    creds_headers = {
        "Authorization": f"pf-sso {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    log.info("[transsion] get_models_from_credentials via control plane")
    result = _post_json(
        creds_url, creds_payload, headers=creds_headers,
        timeout=timeout, op="list_models_refresh",
    )
    raw_models: list[dict] = result.get("models", [])
    chat_models = [
        m["model"] for m in raw_models
        if isinstance(m, dict)
        and "chat/completions" in (m.get("supportedAPIs") or [])
    ]
    if not chat_models:
        chat_models = [m["model"] for m in raw_models if isinstance(m, dict) and "model" in m]
    log.info("[transsion] get_models_from_credentials → %d model(s)", len(chat_models))
    return chat_models


def get_quota_remaining(access_token: str, token_key: str, timeout: int = 30) -> dict:
    """POST /assistant/vendor-api/v1/oneapi/token/quota-remaining

    Returns the raw payload dict with fields:
      name, status, unlimitedQuota, remainQuota, usedQuota,
      monthlyQuota, monthlyUsed, monthlyRemaining, quotaRefreshedAt,
      expiredTime, tokenId, etc.
    Raises ProviderError on failure.
    """
    url = f"{_AUTH_BASE}/assistant/vendor-api/v1/oneapi/token/quota-remaining"
    payload = {
        "metadata": _make_metadata(),
        "payload": {"tokenKey": token_key},
    }
    headers = {
        "Authorization": f"pf-sso {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    log.info("[transsion] get_quota_remaining: token_key=%s...%s", token_key[:4], token_key[-4:])
    return _post_json(url, payload, headers=headers, timeout=timeout, op="get_quota_remaining")


class TranssionProvider(OpenAIRawProvider):
    """Transsion / TEX AI Router — OpenAI-compatible LLM endpoint.

    Credentials (api_key + base_url) are obtained via the two-step auth flow
    in this module and stored in hushclaw.toml by the server handler.
    This class is just OpenAIRawProvider pointed at the TEX router.
    """

    name = "transsion"
    # TEX model IDs are vendor-qualified (e.g. azure/gpt-4o-mini); never use bare gpt-4o-mini.
    _DEFAULT_CHAT_MODEL = "azure/gpt-4o-mini"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        timeout: int = 120,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        normalized_base = _normalize_router_base(base_url)
        super().__init__(
            api_key=api_key,
            base_url=normalized_base,
            timeout=timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            provider_label="transsion",
        )

    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        model = model or self._DEFAULT_CHAT_MODEL
        return await super().complete(messages, system, tools, max_tokens, model)
