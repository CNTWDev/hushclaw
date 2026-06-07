"""Reddit App Connector using the official Reddit OAuth API."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from hushclaw.app_connectors.base import AppConnector, ConnectorManifest
from hushclaw.tools.base import ToolResult
from hushclaw.util.ssl_context import make_ssl_context

API = "https://oauth.reddit.com"


def _token(config, secrets) -> str:
    return secrets.get(getattr(config, "access_token_ref", "app_connectors.reddit.access_token"))


def _headers(config, token: str) -> dict:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": str(getattr(config, "user_agent", "") or "HushClaw-AppConnector/1.0"),
    }


def _request(config, token: str, path: str, *, method: str = "GET", data: dict | None = None) -> tuple[int, dict | list | str]:
    body = None
    headers = _headers(config, token)
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
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


def _err(status: int, payload) -> ToolResult:
    if isinstance(payload, dict):
        msg = payload.get("message") or payload.get("error") or payload.get("reason")
        if not msg and isinstance(payload.get("json"), dict):
            errors = payload["json"].get("errors")
            if errors:
                msg = str(errors)
    else:
        msg = str(payload)
    return ToolResult.error(f"Reddit API error {status}: {msg or 'request failed'}")


def _ensure_read(config, secrets) -> str | ToolResult:
    if not getattr(config, "enabled", False):
        return ToolResult.error("Reddit app connector is disabled. Enable it in Settings and start a new chat.")
    token = _token(config, secrets)
    if not token:
        return ToolResult.error("Reddit OAuth access token is not configured.")
    return token


def _ensure_write(config, secrets) -> str | ToolResult:
    token = _ensure_read(config, secrets)
    if isinstance(token, ToolResult):
        return token
    if not getattr(config, "allow_actions", False):
        return ToolResult.error("Reddit write actions are disabled. Enable allow_actions for the Reddit connector first.")
    return token


def _post_source(item: dict, fallback_subreddit: str = "") -> dict:
    data = item.get("data", item) if isinstance(item, dict) else {}
    subreddit = data.get("subreddit") or fallback_subreddit
    permalink = data.get("permalink") or ""
    url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink
    return {
        "provider": "reddit",
        "type": "post",
        "subreddit": subreddit,
        "id": data.get("id", ""),
        "name": data.get("name", ""),
        "title": data.get("title", ""),
        "url": url,
    }


def _comment_source(item: dict, fallback_subreddit: str = "") -> dict:
    data = item.get("data", item) if isinstance(item, dict) else {}
    permalink = data.get("permalink") or ""
    url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink
    if not url:
        url = data.get("url") or ""
    return {
        "provider": "reddit",
        "type": "comment",
        "subreddit": data.get("subreddit") or fallback_subreddit,
        "id": data.get("id", ""),
        "name": data.get("name", ""),
        "url": url,
    }


class RedditAppConnector(AppConnector):
    manifest = ConnectorManifest(
        id="reddit",
        name="Reddit",
        description="Search, read, post, and comment through Reddit's official OAuth API.",
        capabilities=["search", "read", "post", "comment"],
        auth="Reddit OAuth access token",
        sdk="Reddit OAuth API via stdlib urllib",
        docs_url="https://www.reddit.com/dev/api/",
    )

    def configured(self) -> bool:
        return bool(self.secrets.get(getattr(self.config, "access_token_ref", "app_connectors.reddit.access_token")))

    def tools(self):
        from hushclaw.tools.builtins import reddit_tools

        return [
            reddit_tools.reddit_search._hushclaw_tool,
            reddit_tools.reddit_read._hushclaw_tool,
            reddit_tools.reddit_post._hushclaw_tool,
            reddit_tools.reddit_comment._hushclaw_tool,
        ]


def test_reddit_connection(config, secrets) -> dict:
    token = _token(config, secrets)
    if not token:
        return {"ok": False, "message": "Reddit OAuth access token is not set."}
    status, payload = _request(config, token, "/api/v1/me")
    if status >= 400:
        msg = payload.get("message") if isinstance(payload, dict) else payload
        return {"ok": False, "message": f"Token check failed: {msg}"}
    name = payload.get("name", "unknown") if isinstance(payload, dict) else "unknown"
    return {"ok": True, "message": f"Connected as u/{name}."}


def search(config, secrets, query: str, subreddit: str = "", sort: str = "relevance", limit: int = 5) -> ToolResult:
    token = _ensure_read(config, secrets)
    if isinstance(token, ToolResult):
        return token
    q = str(query or "").strip()
    if not q:
        return ToolResult.error("query is required")
    sr = str(subreddit or getattr(config, "default_subreddit", "") or "").strip().lstrip("r/")
    sort = (sort or "relevance").strip().lower()
    if sort not in {"relevance", "hot", "top", "new", "comments"}:
        sort = "relevance"
    limit = max(1, min(int(limit or 5), 25))
    base = f"/r/{urllib.parse.quote(sr)}/search" if sr else "/search"
    params = {"q": q, "sort": sort, "limit": str(limit), "restrict_sr": "1" if sr else "0"}
    status, payload = _request(config, token, f"{base}?{urllib.parse.urlencode(params)}")
    if status >= 400:
        return _err(status, payload)
    children = (((payload or {}).get("data") or {}).get("children") or []) if isinstance(payload, dict) else []
    sources = [_post_source(item, sr) for item in children[:limit]]
    summary = "\n".join(
        f"- r/{s['subreddit']} {s['name']}: {s['title']}\n  {s['url']}"
        for s in sources
    ) or "No Reddit results found."
    return ToolResult.ok(json.dumps({
        "provider": "reddit",
        "query": q,
        "subreddit": sr,
        "summary": summary,
        "sources": sources,
    }, ensure_ascii=False, indent=2))


def read(config, secrets, target: str, sort: str = "confidence", comment_limit: int = 10) -> ToolResult:
    token = _ensure_read(config, secrets)
    if isinstance(token, ToolResult):
        return token
    target = str(target or "").strip()
    if not target:
        return ToolResult.error("target is required")
    if target.startswith("http"):
        parsed = urllib.parse.urlparse(target)
        path = parsed.path
    else:
        name = target if target.startswith("t3_") else f"t3_{target}"
        path = f"/by_id/{urllib.parse.quote(name)}"
        status, payload = _request(config, token, f"{path}.json")
        if status >= 400:
            return _err(status, payload)
        children = (((payload or {}).get("data") or {}).get("children") or []) if isinstance(payload, dict) else []
        if not children:
            return ToolResult.error("Reddit post was not found.")
        permalink = children[0].get("data", {}).get("permalink", "")
        path = permalink if permalink else path
    params = {"sort": sort or "confidence", "limit": str(max(0, min(int(comment_limit or 10), 50)))}
    status, payload = _request(config, token, f"{path.rstrip('/')}.json?{urllib.parse.urlencode(params)}")
    if status >= 400:
        return _err(status, payload)
    listings = payload if isinstance(payload, list) else []
    post_children = (((listings[0] or {}).get("data") or {}).get("children") or []) if listings else []
    comment_children = (((listings[1] or {}).get("data") or {}).get("children") or []) if len(listings) > 1 else []
    if not post_children:
        return ToolResult.error("Reddit post was not found.")
    post = post_children[0].get("data", {})
    comments = []
    for item in comment_children[: max(0, min(int(comment_limit or 10), 50))]:
        data = item.get("data", {})
        if not data or item.get("kind") != "t1":
            continue
        comments.append({
            "id": data.get("id", ""),
            "name": data.get("name", ""),
            "author": data.get("author", ""),
            "body": data.get("body", ""),
            "score": data.get("score", 0),
            "url": f"https://www.reddit.com{data.get('permalink', '')}" if data.get("permalink") else "",
        })
    source = _post_source({"data": post})
    return ToolResult.ok(json.dumps({
        "provider": "reddit",
        "type": "post",
        "id": post.get("id", ""),
        "name": post.get("name", ""),
        "subreddit": post.get("subreddit", ""),
        "title": post.get("title", ""),
        "author": post.get("author", ""),
        "selftext": post.get("selftext", ""),
        "url": source["url"],
        "score": post.get("score", 0),
        "num_comments": post.get("num_comments", 0),
        "comments": comments,
        "sources": [source] + [_comment_source(c, post.get("subreddit", "")) for c in comments],
    }, ensure_ascii=False, indent=2))


def post(config, secrets, subreddit: str, title: str, body: str = "", url: str = "") -> ToolResult:
    token = _ensure_write(config, secrets)
    if isinstance(token, ToolResult):
        return token
    sr = str(subreddit or getattr(config, "default_subreddit", "") or "").strip().lstrip("r/")
    title = str(title or "").strip()
    body = str(body or "").strip()
    url = str(url or "").strip()
    if not sr:
        return ToolResult.error("subreddit is required")
    if not title:
        return ToolResult.error("title is required")
    kind = "link" if url else "self"
    data = {"sr": sr, "title": title, "kind": kind, "api_type": "json"}
    if url:
        data["url"] = url
    else:
        data["text"] = body
    status, payload = _request(config, token, "/api/submit", method="POST", data=data)
    if status >= 400:
        return _err(status, payload)
    return ToolResult.ok(json.dumps({
        "provider": "reddit",
        "action": "post",
        "subreddit": sr,
        "result": payload,
    }, ensure_ascii=False, indent=2))


def comment(config, secrets, parent: str, body: str) -> ToolResult:
    token = _ensure_write(config, secrets)
    if isinstance(token, ToolResult):
        return token
    parent = str(parent or "").strip()
    body = str(body or "").strip()
    if not parent:
        return ToolResult.error("parent is required. Use a Reddit fullname like t3_postid or t1_commentid.")
    if not body:
        return ToolResult.error("body is required")
    status, payload = _request(config, token, "/api/comment", method="POST", data={
        "thing_id": parent,
        "text": body,
        "api_type": "json",
    })
    if status >= 400:
        return _err(status, payload)
    return ToolResult.ok(json.dumps({
        "provider": "reddit",
        "action": "comment",
        "parent": parent,
        "result": payload,
    }, ensure_ascii=False, indent=2))
