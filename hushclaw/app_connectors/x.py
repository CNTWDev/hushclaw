"""X App Connector using the official X API v2."""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from hushclaw.app_connectors.base import AppConnector, ConnectorManifest
from hushclaw.tools.base import ToolResult
from hushclaw.util.logging import get_logger
from hushclaw.util.ssl_context import make_ssl_context

API = "https://api.x.com/2"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
log = get_logger("app_connectors.x")


def _bearer(config, secrets) -> str:
    return secrets.get(getattr(config, "bearer_token_ref", "app_connectors.x.bearer_token"))


def _access_token(config, secrets) -> str:
    return secrets.get(getattr(config, "access_token_ref", "app_connectors.x.access_token"))


def _refresh_token(config, secrets) -> str:
    return secrets.get(getattr(config, "refresh_token_ref", "app_connectors.x.refresh_token"))


def _oauth_client_id(config, secrets) -> str:
    return secrets.get(getattr(config, "oauth_client_id_ref", "app_connectors.x.oauth_client_id"))


def _oauth_client_secret(config, secrets) -> str:
    return secrets.get(getattr(config, "oauth_client_secret_ref", "app_connectors.x.oauth_client_secret"))


def _store_secret(secrets, ref: str, value: str) -> None:
    if hasattr(secrets, "set"):
        secrets.set(ref, value)


def _request(token: str, path: str, *, method: str = "GET", data: dict | None = None) -> tuple[int, dict | list | str]:
    body = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "HushClaw-AppConnector/1.0",
    }
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20, context=make_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = raw
        return exc.code, payload


def refresh_access_token(config, secrets) -> str | ToolResult:
    refresh_token = _refresh_token(config, secrets)
    if not refresh_token:
        return ToolResult.error("X OAuth access token is expired and no refresh token is configured. Reconnect X user OAuth.")
    client_id = _oauth_client_id(config, secrets)
    if not client_id:
        return ToolResult.error("X OAuth 2.0 client ID is required to refresh the access token.")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "HushClaw-AppConnector/1.0",
    }
    client_secret = _oauth_client_secret(config, secrets)
    if client_secret:
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {basic}"

    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }).encode("utf-8")
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers=headers)
    log.info("Refreshing X OAuth access token")
    try:
        with urllib.request.urlopen(req, timeout=20, context=make_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": raw}
        msg = payload.get("error_description") or payload.get("error") or payload.get("message") or raw
        log.warning("X OAuth token refresh failed: %s", msg)
        return ToolResult.error(f"X OAuth token refresh failed: {msg}. Reconnect X user OAuth.")
    except Exception as exc:
        log.warning("X OAuth token refresh failed: %s", exc)
        return ToolResult.error(f"X OAuth token refresh failed: {exc}")

    access = str(payload.get("access_token") or "").strip()
    refresh = str(payload.get("refresh_token") or "").strip()
    if not access:
        return ToolResult.error("X OAuth token refresh did not return an access token. Reconnect X user OAuth.")
    _store_secret(secrets, getattr(config, "access_token_ref", "app_connectors.x.access_token"), access)
    if refresh:
        _store_secret(secrets, getattr(config, "refresh_token_ref", "app_connectors.x.refresh_token"), refresh)
    log.info("X OAuth token refresh succeeded")
    return access


def _user_token(config, secrets) -> str | ToolResult:
    token = _access_token(config, secrets)
    if token:
        return token
    return refresh_access_token(config, secrets)


def _request_user_context(
    config,
    secrets,
    path: str,
    *,
    method: str = "GET",
    data: dict | None = None,
    token: str = "",
) -> tuple[int, dict | list | str] | ToolResult:
    token = token or _user_token(config, secrets)
    if isinstance(token, ToolResult):
        return token
    status, payload = _request(token, path, method=method, data=data)
    if status != 401:
        return status, payload
    refreshed = refresh_access_token(config, secrets)
    if isinstance(refreshed, ToolResult):
        return refreshed
    return _request(refreshed, path, method=method, data=data)


def _err(status: int, payload) -> ToolResult:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        msg = payload.get("detail") or payload.get("title") or payload.get("message")
        if not msg and errors:
            msg = str(errors)
    else:
        msg = str(payload)
    return ToolResult.error(f"X API error {status}: {msg or 'request failed'}")


def _ensure_read(config, secrets) -> tuple[str, str] | ToolResult:
    if not getattr(config, "enabled", False):
        return ToolResult.error("X app connector is disabled. Enable it in Settings and start a new chat.")
    bearer = _bearer(config, secrets)
    if bearer:
        return "bearer", bearer
    token = _user_token(config, secrets)
    if isinstance(token, ToolResult):
        return token
    return "user", token


def _ensure_write(config, secrets) -> str | ToolResult:
    if not getattr(config, "enabled", False):
        return ToolResult.error("X app connector is disabled. Enable it in Settings and start a new chat.")
    if not getattr(config, "allow_actions", False):
        return ToolResult.error("X write actions are disabled. Enable allow_actions for the X connector first.")
    return _user_token(config, secrets)


def _publish_post(config, secrets, text: str) -> ToolResult:
    token = _ensure_write(config, secrets)
    if isinstance(token, ToolResult):
        log.warning("X post blocked before API call: %s", token.content)
        return token
    log.info("Posting X draft via /2/tweets text_len=%s", len(text))
    result = _request_user_context(config, secrets, "/tweets", method="POST", data={"text": text}, token=token)
    if isinstance(result, ToolResult):
        return result
    status, payload = result
    log.info("X post API returned status=%s", status)
    if status >= 400:
        return _err(status, payload)
    return ToolResult.ok(json.dumps({
        "provider": "x",
        "action": "post",
        "result": payload,
    }, ensure_ascii=False, indent=2))


def _publish_reply(config, secrets, post_id: str, text: str) -> ToolResult:
    token = _ensure_write(config, secrets)
    if isinstance(token, ToolResult):
        log.warning("X reply blocked before API call: %s", token.content)
        return token
    log.info("Posting X reply via /2/tweets post_id=%s text_len=%s", post_id, len(text))
    result = _request_user_context(config, secrets, "/tweets", method="POST", data={
        "text": text,
        "reply": {"in_reply_to_tweet_id": post_id},
    }, token=token)
    if isinstance(result, ToolResult):
        return result
    status, payload = result
    log.info("X reply API returned status=%s", status)
    if status >= 400:
        return _err(status, payload)
    return ToolResult.ok(json.dumps({
        "provider": "x",
        "action": "reply",
        "post_id": post_id,
        "result": payload,
    }, ensure_ascii=False, indent=2))


def _draft(memory_store, *, action: str, text: str, post_id: str = "") -> ToolResult:
    if memory_store is None:
        return ToolResult.error("X publish confirmation requires the local memory store.")
    title = "X post draft" if action == "post" else f"X reply draft to {post_id}"
    event = memory_store.upsert_app_inbox_event(
        connector_id="x",
        event_type=f"draft.{action}",
        title=title,
        body=text,
        source_url=f"https://x.com/i/web/status/{post_id}" if post_id else "",
        payload={"provider": "x", "action": action, "text": text, "post_id": post_id},
        status="pending",
    )
    return ToolResult.ok(json.dumps({
        "provider": "x",
        "action": action,
        "status": "pending_confirmation",
        "draft_event_id": event.get("event_id", ""),
        "message": "Created a pending X draft. Publish it from the App Connector inbox after review.",
    }, ensure_ascii=False, indent=2))


def _tweet_source(tweet: dict) -> dict:
    author_id = tweet.get("author_id", "")
    tid = tweet.get("id", "")
    return {
        "provider": "x",
        "type": "post",
        "id": tid,
        "author_id": author_id,
        "title": (tweet.get("text") or "")[:120],
        "url": f"https://x.com/i/web/status/{tid}" if tid else "",
    }


class XAppConnector(AppConnector):
    manifest = ConnectorManifest(
        id="x",
        name="X",
        description="Search, read, stream, post, and reply through the official X API v2.",
        capabilities=["search", "read", "stream", "post", "reply"],
        auth="X API v2 bearer token and OAuth 2.0 user access token",
        sdk="X API v2 via stdlib urllib",
        docs_url="https://docs.x.com/x-api",
    )

    def configured(self) -> bool:
        return bool(
            self.secrets.get(getattr(self.config, "bearer_token_ref", "app_connectors.x.bearer_token"))
            or self.secrets.get(getattr(self.config, "access_token_ref", "app_connectors.x.access_token"))
            or self.secrets.get(getattr(self.config, "refresh_token_ref", "app_connectors.x.refresh_token"))
        )

    def tools(self):
        from hushclaw.tools.builtins import x_tools

        return [
            x_tools.x_search._hushclaw_tool,
            x_tools.x_read_post._hushclaw_tool,
            x_tools.x_post._hushclaw_tool,
            x_tools.x_reply._hushclaw_tool,
        ]


def test_x_connection(config, secrets) -> dict:
    if not getattr(config, "enabled", False):
        return {"ok": False, "message": "X app connector is disabled."}

    access_token = _access_token(config, secrets)
    if access_token:
        result = _request_user_context(config, secrets, "/users/me")
        if isinstance(result, ToolResult):
            return {"ok": False, "message": result.content}
        status, payload = result
        if status >= 400:
            msg = payload.get("detail") if isinstance(payload, dict) else payload
            return {"ok": False, "message": f"OAuth user token check failed: {msg}"}
        user = (payload.get("data") or {}) if isinstance(payload, dict) else {}
        return {"ok": True, "message": f"Connected with user context as @{user.get('username', 'unknown')}."}

    refresh_token = _refresh_token(config, secrets)
    if refresh_token:
        result = _request_user_context(config, secrets, "/users/me")
        if isinstance(result, ToolResult):
            return {"ok": False, "message": result.content}
        status, payload = result
        if status >= 400:
            msg = payload.get("detail") if isinstance(payload, dict) else payload
            return {"ok": False, "message": f"OAuth user token check failed after refresh: {msg}"}
        user = (payload.get("data") or {}) if isinstance(payload, dict) else {}
        return {"ok": True, "message": f"Connected with refreshed user context as @{user.get('username', 'unknown')}."}

    bearer = _bearer(config, secrets)
    if bearer:
        params = urllib.parse.urlencode({
            "query": "from:XDevelopers -is:retweet",
            "max_results": "10",
        })
        status, payload = _request(bearer, f"/tweets/search/recent?{params}")
        if status >= 400:
            msg = payload.get("detail") if isinstance(payload, dict) else payload
            return {"ok": False, "message": f"Bearer token check failed: {msg}"}
        return {
            "ok": True,
            "message": "Bearer token is valid for app-only read APIs. User OAuth is still required for posting/replying and /users/me.",
        }

    return {"ok": False, "message": "X bearer token or OAuth user access token is not configured."}


def search(config, secrets, query: str, limit: int = 10, recent: bool = True) -> ToolResult:
    auth = _ensure_read(config, secrets)
    if isinstance(auth, ToolResult):
        return auth
    q = str(query or "").strip()
    if not q:
        return ToolResult.error("query is required")
    limit = max(10, min(int(limit or 10), 100))
    endpoint = "/tweets/search/recent" if recent else "/tweets/search/all"
    params = {
        "query": q,
        "max_results": str(limit),
        "tweet.fields": "author_id,created_at,public_metrics,conversation_id",
    }
    path = f"{endpoint}?{urllib.parse.urlencode(params)}"
    auth_type, token = auth
    if auth_type == "user":
        result = _request_user_context(config, secrets, path, token=token)
        if isinstance(result, ToolResult):
            return result
        status, payload = result
    else:
        status, payload = _request(token, path)
    if status >= 400:
        return _err(status, payload)
    tweets = payload.get("data", []) if isinstance(payload, dict) else []
    sources = [_tweet_source(tweet) for tweet in tweets]
    summary = "\n".join(
        f"- {tweet.get('id')}: {(tweet.get('text') or '').replace(chr(10), ' ')[:180]}\n  https://x.com/i/web/status/{tweet.get('id')}"
        for tweet in tweets
    ) or "No X results found."
    return ToolResult.ok(json.dumps({
        "provider": "x",
        "query": q,
        "summary": summary,
        "posts": tweets,
        "sources": sources,
    }, ensure_ascii=False, indent=2))


def read_post(config, secrets, post_id: str) -> ToolResult:
    auth = _ensure_read(config, secrets)
    if isinstance(auth, ToolResult):
        return auth
    post_id = str(post_id or "").strip()
    if not post_id:
        return ToolResult.error("post_id is required")
    params = {"tweet.fields": "author_id,created_at,public_metrics,conversation_id,referenced_tweets"}
    path = f"/tweets/{urllib.parse.quote(post_id)}?{urllib.parse.urlencode(params)}"
    auth_type, token = auth
    if auth_type == "user":
        result = _request_user_context(config, secrets, path, token=token)
        if isinstance(result, ToolResult):
            return result
        status, payload = result
    else:
        status, payload = _request(token, path)
    if status >= 400:
        return _err(status, payload)
    tweet = (payload.get("data") or {}) if isinstance(payload, dict) else {}
    return ToolResult.ok(json.dumps({
        "provider": "x",
        "type": "post",
        "post": tweet,
        "sources": [_tweet_source(tweet)] if tweet else [],
    }, ensure_ascii=False, indent=2))


def post(config, secrets, text: str, memory_store=None) -> ToolResult:
    text = str(text or "").strip()
    if not text:
        return ToolResult.error("text is required")
    if getattr(config, "require_publish_confirmation", True):
        if not getattr(config, "enabled", False):
            return ToolResult.error("X app connector is disabled. Enable it in Settings and start a new chat.")
        return _draft(memory_store, action="post", text=text)
    return _publish_post(config, secrets, text)


def reply(config, secrets, post_id: str, text: str, memory_store=None) -> ToolResult:
    post_id = str(post_id or "").strip()
    text = str(text or "").strip()
    if not post_id:
        return ToolResult.error("post_id is required")
    if not text:
        return ToolResult.error("text is required")
    if getattr(config, "require_publish_confirmation", True):
        if not getattr(config, "enabled", False):
            return ToolResult.error("X app connector is disabled. Enable it in Settings and start a new chat.")
        return _draft(memory_store, action="reply", post_id=post_id, text=text)
    return _publish_reply(config, secrets, post_id, text)


def load_publishable_draft(memory_store, event_id: str) -> dict:
    if memory_store is None:
        return {"ok": False, "message": "Memory store is unavailable."}
    event = memory_store.get_app_inbox_event(event_id)
    if not event:
        return {"ok": False, "message": "Draft not found."}
    if event.get("connector_id") != "x" or not str(event.get("event_type", "")).startswith("draft."):
        return {"ok": False, "message": "Event is not an X draft."}
    if event.get("status") != "pending":
        return {"ok": False, "message": f"Draft is {event.get('status') or 'not pending'}."}
    payload = event.get("payload") or {}
    action = str(payload.get("action") or "").strip()
    text = str(payload.get("text") or event.get("body") or "").strip()
    post_id = str(payload.get("post_id") or "").strip()
    if not text:
        return {"ok": False, "message": "Draft text is empty."}
    if action == "reply" and not post_id:
        return {"ok": False, "message": "Reply draft is missing post_id."}
    return {
        "ok": True,
        "event": event,
        "action": action if action in {"post", "reply"} else "post",
        "text": text,
        "post_id": post_id,
    }


def publish_loaded_draft(config, secrets, draft: dict) -> ToolResult:
    action = str(draft.get("action") or "post")
    text = str(draft.get("text") or "").strip()
    post_id = str(draft.get("post_id") or "").strip()
    if action == "reply":
        return _publish_reply(config, secrets, post_id, text)
    return _publish_post(config, secrets, text)


def mark_draft_published(memory_store, event_id: str, result: ToolResult) -> dict:
    if result.is_error:
        return {"ok": False, "message": result.content}
    updated = memory_store.update_app_inbox_event_status(event_id, "published")
    return {"ok": True, "message": "Published to X.", "result": result.content, "item": updated}


def publish_draft(config, secrets, memory_store, event_id: str) -> dict:
    draft = load_publishable_draft(memory_store, event_id)
    if not draft.get("ok"):
        return draft
    result = publish_loaded_draft(config, secrets, draft)
    return mark_draft_published(memory_store, event_id, result)
