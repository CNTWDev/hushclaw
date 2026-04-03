"""Transsion / TEX AI Router provider.

Two-step auth flow:
  1. send_email_code(email)  — sends OTP to the user's email
  2. acquire_credentials(email, code)  — logs in, exchanges accessToken for sk-xxx + baseUrl

After credential acquisition, LLM calls use the standard OpenAI-compatible
chat/completions API at airouter.aibotplatform.com, handled by OpenAIRawProvider.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from hushclaw.exceptions import ProviderError
from hushclaw.providers.openai_raw import OpenAIRawProvider
from hushclaw.util.logging import get_logger
from hushclaw.util.ssl_context import make_ssl_context

log = get_logger("providers.transsion")

_AUTH_BASE = "https://bus-test-feature.aibotplatform.com"
_APP_ID = "jwouyypn"
_DEFAULT_ROUTER_BASE = "https://airouter.aibotplatform.com"


def _make_metadata(request_id: str | None = None) -> dict:
    rid = request_id or f"req_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{uuid.uuid4().hex[:10]}"
    return {
        "appID": _APP_ID,
        "requestID": rid,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
    }


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode()
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=make_ssl_context()) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_str = e.read().decode("utf-8", errors="replace")
        raise ProviderError(f"Transsion auth HTTP {e.code}: {body_str[:400]}") from e
    except Exception as e:
        raise ProviderError(f"Transsion auth request failed: {e}") from e

    meta = body.get("metadata", {})
    code = meta.get("code", 0)
    if code != 200:
        debug = meta.get("debugMessage", "")
        raise ProviderError(f"Transsion API error (code={code}): {debug or body}")
    return body.get("payload", {})


def send_email_code(email: str, timeout: int = 30) -> None:
    """Send OTP verification code to *email*.

    Raises ProviderError on failure.
    """
    url = f"{_AUTH_BASE}/assistant/vendor-api/v1/auth/send-email-code"
    payload = {
        "metadata": _make_metadata(),
        "payload": {"email": email},
    }
    log.info("[transsion] send_email_code: email=%s", email)
    result = _post_json(url, payload, timeout=timeout)
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
    log.info("[transsion] acquire_credentials: login email=%s", email)
    login_result = _post_json(login_url, login_payload, timeout=timeout)
    access_token: str = login_result.get("accessToken", "")
    display_name: str = login_result.get("displayName", "")
    if not access_token:
        raise ProviderError("Transsion login succeeded but no accessToken returned")

    # Step 2: acquire API credentials using accessToken
    creds_url = f"{_AUTH_BASE}/assistant/vendor-api/v1/oneapi/api-credentials/info"
    creds_payload = {
        "metadata": _make_metadata(request_id="hushclaw-acquire"),
        "payload": {},
    }
    creds_headers = {
        "Authorization": f"pf-sso {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    log.info("[transsion] acquiring API credentials")
    creds_result = _post_json(creds_url, creds_payload, headers=creds_headers, timeout=timeout)

    token_info: dict = creds_result.get("tokenInfo", {})
    api_key: str = token_info.get("key", "")
    remain_quota = str(token_info.get("remainQuota", ""))
    base_url: str = creds_result.get("baseUrl", _DEFAULT_ROUTER_BASE).rstrip("/")
    raw_models: list[dict] = creds_result.get("models", [])

    if not api_key:
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


class TranssionProvider(OpenAIRawProvider):
    """Transsion / TEX AI Router — OpenAI-compatible LLM endpoint.

    Credentials (api_key + base_url) are obtained via the two-step auth flow
    in this module and stored in hushclaw.toml by the server handler.
    This class is just OpenAIRawProvider pointed at the TEX router.
    """

    name = "transsion"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        timeout: int = 120,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url or f"{_DEFAULT_ROUTER_BASE}/v1",
            timeout=timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            provider_label="transsion",
        )
