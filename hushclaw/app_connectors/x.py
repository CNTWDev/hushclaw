"""X App Connector using the official X API v2."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from hushclaw.app_connectors.base import AppConnector, ConnectorManifest
from hushclaw.tools.base import ToolResult

API = "https://api.x.com/2"


def _bearer(config, secrets) -> str:
    return secrets.get(getattr(config, "bearer_token_ref", "app_connectors.x.bearer_token"))


def _access_token(config, secrets) -> str:
    return secrets.get(getattr(config, "access_token_ref", "app_connectors.x.access_token"))


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
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = raw
        return exc.code, payload


def _err(status: int, payload) -> ToolResult:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        msg = payload.get("detail") or payload.get("title") or payload.get("message")
        if not msg and errors:
            msg = str(errors)
    else:
        msg = str(payload)
    return ToolResult.error(f"X API error {status}: {msg or 'request failed'}")


def _ensure_read(config, secrets) -> str | ToolResult:
    if not getattr(config, "enabled", False):
        return ToolResult.error("X app connector is disabled. Enable it in Settings and start a new chat.")
    token = _bearer(config, secrets) or _access_token(config, secrets)
    if not token:
        return ToolResult.error("X bearer/access token is not configured.")
    return token


def _ensure_write(config, secrets) -> str | ToolResult:
    if not getattr(config, "enabled", False):
        return ToolResult.error("X app connector is disabled. Enable it in Settings and start a new chat.")
    token = _access_token(config, secrets)
    if not token:
        return ToolResult.error("X OAuth access token is required for write actions.")
    if not getattr(config, "allow_actions", False):
        return ToolResult.error("X write actions are disabled. Enable allow_actions for the X connector first.")
    return token


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
        description="Search, read, post, and reply through the official X API v2.",
        capabilities=["search", "read", "post", "reply"],
        auth="X API v2 bearer token and OAuth 2.0 user access token",
        sdk="X API v2 via stdlib urllib",
        docs_url="https://docs.x.com/x-api",
    )

    def configured(self) -> bool:
        return bool(
            self.secrets.get(getattr(self.config, "bearer_token_ref", "app_connectors.x.bearer_token"))
            or self.secrets.get(getattr(self.config, "access_token_ref", "app_connectors.x.access_token"))
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
    token = _ensure_read(config, secrets)
    if isinstance(token, ToolResult):
        return {"ok": False, "message": token.content}
    status, payload = _request(token, "/users/me")
    if status >= 400:
        msg = payload.get("detail") if isinstance(payload, dict) else payload
        return {"ok": False, "message": f"Token check failed: {msg}"}
    user = (payload.get("data") or {}) if isinstance(payload, dict) else {}
    return {"ok": True, "message": f"Connected as @{user.get('username', 'unknown')}."}


def search(config, secrets, query: str, limit: int = 10, recent: bool = True) -> ToolResult:
    token = _ensure_read(config, secrets)
    if isinstance(token, ToolResult):
        return token
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
    status, payload = _request(token, f"{endpoint}?{urllib.parse.urlencode(params)}")
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
    token = _ensure_read(config, secrets)
    if isinstance(token, ToolResult):
        return token
    post_id = str(post_id or "").strip()
    if not post_id:
        return ToolResult.error("post_id is required")
    params = {"tweet.fields": "author_id,created_at,public_metrics,conversation_id,referenced_tweets"}
    status, payload = _request(token, f"/tweets/{urllib.parse.quote(post_id)}?{urllib.parse.urlencode(params)}")
    if status >= 400:
        return _err(status, payload)
    tweet = (payload.get("data") or {}) if isinstance(payload, dict) else {}
    return ToolResult.ok(json.dumps({
        "provider": "x",
        "type": "post",
        "post": tweet,
        "sources": [_tweet_source(tweet)] if tweet else [],
    }, ensure_ascii=False, indent=2))


def post(config, secrets, text: str) -> ToolResult:
    token = _ensure_write(config, secrets)
    if isinstance(token, ToolResult):
        return token
    text = str(text or "").strip()
    if not text:
        return ToolResult.error("text is required")
    status, payload = _request(token, "/tweets", method="POST", data={"text": text})
    if status >= 400:
        return _err(status, payload)
    return ToolResult.ok(json.dumps({
        "provider": "x",
        "action": "post",
        "result": payload,
    }, ensure_ascii=False, indent=2))


def reply(config, secrets, post_id: str, text: str) -> ToolResult:
    token = _ensure_write(config, secrets)
    if isinstance(token, ToolResult):
        return token
    post_id = str(post_id or "").strip()
    text = str(text or "").strip()
    if not post_id:
        return ToolResult.error("post_id is required")
    if not text:
        return ToolResult.error("text is required")
    status, payload = _request(token, "/tweets", method="POST", data={
        "text": text,
        "reply": {"in_reply_to_tweet_id": post_id},
    })
    if status >= 400:
        return _err(status, payload)
    return ToolResult.ok(json.dumps({
        "provider": "x",
        "action": "reply",
        "post_id": post_id,
        "result": payload,
    }, ensure_ascii=False, indent=2))
