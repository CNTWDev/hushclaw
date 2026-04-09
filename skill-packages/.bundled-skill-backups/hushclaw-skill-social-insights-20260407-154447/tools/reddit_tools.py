"""Reddit tools — uses Reddit's OAuth2 API for structured data access.

Reddit requires authentication since 2023 (even for read-only public data).
Setup is free and takes ~2 minutes:

1. Create a Reddit account (or use existing)
2. Go to https://www.reddit.com/prefs/apps and create a new "script" app
3. Set environment variables:
   export REDDIT_CLIENT_ID="your_app_client_id"
   export REDDIT_CLIENT_SECRET="your_app_client_secret"
   export REDDIT_USER_AGENT="HushClaw:social-insights:1.0 (by /u/your_username)"

Rate limit: 60 requests/minute for authenticated apps.
"""
from __future__ import annotations

import json
import os
import textwrap
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from hushclaw.tools.base import ToolResult, tool

_OAUTH_URL = "https://www.reddit.com/api/v1/access_token"
_API_BASE = "https://oauth.reddit.com"
_ID_ENV = "REDDIT_CLIENT_ID"
_SECRET_ENV = "REDDIT_CLIENT_SECRET"
_UA_ENV = "REDDIT_USER_AGENT"
_DEFAULT_UA = "HushClaw:social-insights:1.0"
_INSTALL_HINT = (
    "Reddit API credentials not configured.\n"
    "Setup (free, ~2 minutes):\n"
    "1. Create a Reddit account at https://www.reddit.com\n"
    "2. Go to https://www.reddit.com/prefs/apps → create a 'script' app\n"
    "3. Set environment variables:\n"
    "   export REDDIT_CLIENT_ID='your_app_client_id'\n"
    "   export REDDIT_CLIENT_SECRET='your_app_client_secret'\n"
    "   export REDDIT_USER_AGENT='HushClaw:social-insights:1.0 (by /u/your_username)'"
)

# In-process token cache: {"token": str, "expires_at": float}
_token_cache: dict = {}


def _get_token() -> tuple[str, str | None]:
    """Obtain a Reddit OAuth2 app-only access token (client credentials)."""
    client_id = os.environ.get(_ID_ENV, "").strip()
    client_secret = os.environ.get(_SECRET_ENV, "").strip()
    if not client_id:
        return "", _INSTALL_HINT

    # Return cached token if still valid (with 60s buffer)
    now = time.time()
    if _token_cache.get("token") and _token_cache.get("expires_at", 0) > now + 60:
        return _token_cache["token"], None

    ua = os.environ.get(_UA_ENV, _DEFAULT_UA)
    import base64
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
    }).encode()
    req = urllib.request.Request(
        _OAUTH_URL,
        data=data,
        headers={
            "Authorization": f"Basic {credentials}",
            "User-Agent": ua,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
    except Exception as e:
        return "", f"Failed to authenticate with Reddit: {e}"

    if "access_token" not in body:
        return "", f"Reddit auth failed: {body.get('message') or body.get('error') or body}"

    token = body["access_token"]
    expires_in = body.get("expires_in", 3600)
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expires_in
    return token, None


def _get(path: str, params: dict) -> tuple[int, dict]:
    """GET from Reddit OAuth API."""
    token, err = _get_token()
    if err:
        return -2, {"error": err}

    ua = os.environ.get(_UA_ENV, _DEFAULT_UA)
    qs = urllib.parse.urlencode(params)
    url = f"{_API_BASE}{path}?{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": ua,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.request.HTTPError as e:
        return e.code, {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return -1, {"error": str(e)}


def _fmt_ts(utc: float) -> str:
    return datetime.fromtimestamp(utc, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _post_to_dict(post: dict) -> dict:
    d = post.get("data", {})
    text = d.get("selftext", "") or ""
    return {
        "id": d.get("id", ""),
        "title": d.get("title", ""),
        "url": d.get("url", ""),
        "permalink": f"https://www.reddit.com{d.get('permalink', '')}",
        "author": d.get("author", ""),
        "score": d.get("score", 0),
        "upvote_ratio": d.get("upvote_ratio", 0),
        "num_comments": d.get("num_comments", 0),
        "subreddit": d.get("subreddit", ""),
        "created_utc": _fmt_ts(d.get("created_utc", 0)),
        "selftext": textwrap.shorten(text, width=400, placeholder="…") if text else "",
        "flair": d.get("link_flair_text", "") or "",
    }


def _collect_comments(items: list, depth: int, max_depth: int) -> list:
    """Recursively flatten the Reddit comment tree up to max_depth."""
    results = []
    for item in items:
        kind = item.get("kind", "")
        if kind == "more":
            continue
        d = item.get("data", {})
        body = d.get("body", "")
        if not body or body in ("[deleted]", "[removed]"):
            continue
        entry = {
            "id": d.get("id", ""),
            "author": d.get("author", ""),
            "score": d.get("score", 0),
            "created_utc": _fmt_ts(d.get("created_utc", 0)),
            "body": textwrap.shorten(body, width=800, placeholder="…"),
            "depth": depth,
        }
        results.append(entry)
        if depth < max_depth:
            replies = d.get("replies", "")
            if isinstance(replies, dict):
                child_items = replies.get("data", {}).get("children", [])
                results.extend(_collect_comments(child_items, depth + 1, max_depth))
    return results


@tool(description=(
    "Get posts from a Reddit subreddit. "
    "sort: hot|new|top|rising (default: hot). "
    "time_filter: hour|day|week|month|year|all — only applies when sort=top. "
    "Requires REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET environment variables."
))
def reddit_posts(
    subreddit: str,
    sort: str = "hot",
    limit: int = 10,
    time_filter: str = "day",
) -> ToolResult:
    """Fetch posts from r/{subreddit} using Reddit's OAuth API."""
    if not subreddit.strip():
        return ToolResult.error("subreddit cannot be empty")
    sort = sort.lower()
    if sort not in ("hot", "new", "top", "rising"):
        return ToolResult.error(f"Invalid sort '{sort}'. Use: hot, new, top, rising")
    limit = max(1, min(limit, 100))

    params: dict = {"limit": limit, "raw_json": 1}
    if sort == "top":
        params["t"] = time_filter

    status, data = _get(f"/r/{urllib.parse.quote(subreddit)}/{sort}", params)
    if status == -2:
        return ToolResult.error(data["error"])
    if status == -1:
        return ToolResult.error(data["error"])
    if status == 403:
        return ToolResult.error(f"r/{subreddit} is private or quarantined.")
    if status == 404:
        return ToolResult.error(f"r/{subreddit} not found.")
    if status != 200:
        return ToolResult.error(f"Reddit API error {status}: {data.get('error', data)}")

    posts = [_post_to_dict(p) for p in data.get("data", {}).get("children", [])]
    return ToolResult.ok(json.dumps({
        "subreddit": subreddit,
        "sort": sort,
        "count": len(posts),
        "posts": posts,
    }, ensure_ascii=False, indent=2))


@tool(description=(
    "Search Reddit posts by keyword. "
    "Optionally restrict to a subreddit with the subreddit parameter. "
    "sort: relevance|hot|new|top|comments (default: relevance). "
    "Requires REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET environment variables."
))
def reddit_search(
    query: str,
    subreddit: str = "",
    sort: str = "relevance",
    limit: int = 10,
) -> ToolResult:
    """Search Reddit posts using the OAuth API."""
    if not query.strip():
        return ToolResult.error("query cannot be empty")
    sort = sort.lower()
    if sort not in ("relevance", "hot", "new", "top", "comments"):
        return ToolResult.error(f"Invalid sort '{sort}'. Use: relevance, hot, new, top, comments")
    limit = max(1, min(limit, 100))

    params = {"q": query, "sort": sort, "limit": limit, "type": "link", "raw_json": 1}
    if subreddit.strip():
        params["restrict_sr"] = 1
        path = f"/r/{urllib.parse.quote(subreddit.strip())}/search"
    else:
        path = "/search"

    status, data = _get(path, params)
    if status == -2:
        return ToolResult.error(data["error"])
    if status == -1:
        return ToolResult.error(data["error"])
    if status != 200:
        return ToolResult.error(f"Reddit search error {status}: {data.get('error', data)}")

    posts = [_post_to_dict(p) for p in data.get("data", {}).get("children", [])]
    return ToolResult.ok(json.dumps({
        "query": query,
        "subreddit_filter": subreddit or None,
        "sort": sort,
        "count": len(posts),
        "posts": posts,
    }, ensure_ascii=False, indent=2))


@tool(description=(
    "Get comments from a Reddit post. "
    "post_url: full Reddit URL or /r/sub/comments/id/ path. "
    "depth: max nesting depth to expand (1-10, default 3). "
    "limit: max top-level comments (default 100). "
    "Requires REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET environment variables."
))
def reddit_comments(
    post_url: str,
    limit: int = 100,
    depth: int = 3,
) -> ToolResult:
    """Fetch and flatten the comment tree for a Reddit post."""
    if not post_url.strip():
        return ToolResult.error("post_url cannot be empty")
    depth = max(1, min(depth, 10))
    limit = max(1, min(limit, 500))

    # Normalise URL to path only
    url = post_url.strip().split("?")[0].split("#")[0].rstrip("/")
    for domain in ("https://old.reddit.com", "https://new.reddit.com",
                   "https://www.reddit.com", "http://reddit.com"):
        url = url.replace(domain, "")
    if not url.startswith("/r/"):
        return ToolResult.error(
            "Cannot parse post_url. Use the full Reddit post URL, e.g. "
            "https://www.reddit.com/r/python/comments/abc123/title/"
        )

    path = url  # e.g. /r/python/comments/abc123/title
    params = {"depth": depth, "limit": limit, "raw_json": 1}

    status, data = _get(path, params)
    if status == -2:
        return ToolResult.error(data["error"])
    if status == -1:
        return ToolResult.error(data["error"])
    if status == 404:
        return ToolResult.error("Post not found or subreddit is private.")
    if status != 200:
        return ToolResult.error(f"Reddit API error {status}: {data.get('error', data)}")

    if not isinstance(data, list) or len(data) < 2:
        return ToolResult.error("Unexpected response format from Reddit")

    post_data = data[0].get("data", {}).get("children", [{}])[0].get("data", {})
    comment_children = data[1].get("data", {}).get("children", [])
    comments = _collect_comments(comment_children, depth=0, max_depth=depth)

    return ToolResult.ok(json.dumps({
        "post_id": post_data.get("id", ""),
        "post_title": post_data.get("title", ""),
        "post_url": post_url,
        "total_comments": post_data.get("num_comments", 0),
        "fetched_comments": len(comments),
        "comments": comments,
    }, ensure_ascii=False, indent=2))
