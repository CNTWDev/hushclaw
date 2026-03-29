"""Facebook tools — Meta Graph API v21.0.

Accesses public Page posts and comments using an App Access Token.
App Access Token format: {app_id}|{app_secret} (no user login needed for public Pages).

Setup:
  1. Create a Facebook App at https://developers.facebook.com
  2. Copy your App ID and App Secret
  3. Set: export FACEBOOK_ACCESS_TOKEN="{app_id}|{app_secret}"

Alternatively, use a Page Access Token for Pages you manage.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from hushclaw.tools.base import ToolResult, tool

_GRAPH = "https://graph.facebook.com/v21.0"
_TOKEN_ENV = "FACEBOOK_ACCESS_TOKEN"
_INSTALL_HINT = (
    "Facebook Access Token not configured.\n"
    "1. Create a free app at https://developers.facebook.com\n"
    "2. Copy your App ID and App Secret from the app dashboard\n"
    "3. Set: export FACEBOOK_ACCESS_TOKEN='{app_id}|{app_secret}'\n"
    "   (Replace {app_id} and {app_secret} with your actual values)\n"
    "Note: App Access Token grants read access to public Pages without user login."
)


def _token() -> str | None:
    return os.environ.get(_TOKEN_ENV, "").strip() or None


def _get(path: str, params: dict) -> tuple[int, dict]:
    """GET from Facebook Graph API."""
    try:
        import httpx
    except ImportError:
        return -1, {"error": "httpx is not installed. Run: pip install httpx"}

    tok = _token()
    if not tok:
        return -2, {"error": _INSTALL_HINT}

    all_params = {"access_token": tok, **params}
    try:
        resp = httpx.get(f"{_GRAPH}/{path.lstrip('/')}", params=all_params, timeout=15)
        return resp.status_code, resp.json()
    except httpx.RequestError as e:
        return -1, {"error": f"Network error: {e}"}


def _fmt_ts(s: str) -> str:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return s


def _graph_error(status: int, data: dict) -> str:
    err = data.get("error", {})
    if isinstance(err, dict):
        msg = err.get("message", "")
        code = err.get("code", "")
        return f"Graph API error {status} (code {code}): {msg}"
    return f"Graph API error {status}: {data}"


@tool(description=(
    "Get recent posts from a public Facebook Page. "
    "page_id: the Page's numeric ID or username (e.g. 'meta' or '123456789'). "
    "Returns post message, publish time, and reaction counts. "
    "Requires FACEBOOK_ACCESS_TOKEN environment variable."
))
def facebook_page_posts(
    page_id: str,
    limit: int = 10,
) -> ToolResult:
    """Fetch posts from a public Facebook Page via Graph API."""
    page_id = page_id.strip()
    if not page_id:
        return ToolResult.error("page_id cannot be empty")
    limit = max(1, min(limit, 100))

    # Fetch page info + posts in one call
    fields = (
        "id,name,fan_count,posts.limit({limit}){{{post_fields}}}"
    ).format(
        limit=limit,
        post_fields="id,message,story,created_time,reactions.summary(true),comments.summary(true),shares",
    )

    status, data = _get(page_id, {"fields": fields})
    if status == -2:
        return ToolResult.error(data["error"])
    if status == -1:
        return ToolResult.error(data["error"])
    if status == 404:
        return ToolResult.error(f"Page '{page_id}' not found. Check the page ID or username.")
    if status != 200:
        return ToolResult.error(_graph_error(status, data))

    page_name = data.get("name", "")
    page_fans = data.get("fan_count", 0)
    posts_raw = (data.get("posts") or {}).get("data") or []

    posts = []
    for p in posts_raw:
        reactions = (p.get("reactions") or {}).get("summary", {})
        comments = (p.get("comments") or {}).get("summary", {})
        shares = p.get("shares", {})
        posts.append({
            "post_id": p.get("id", ""),
            "message": p.get("message") or p.get("story") or "",
            "created_time": _fmt_ts(p.get("created_time", "")),
            "reactions": reactions.get("total_count", 0),
            "comments": comments.get("total_count", 0),
            "shares": shares.get("count", 0) if isinstance(shares, dict) else 0,
            "url": f"https://www.facebook.com/{p.get('id', '').replace('_', '/posts/')}",
        })

    return ToolResult.ok(json.dumps({
        "page_id": page_id,
        "page_name": page_name,
        "page_fans": page_fans,
        "fetched_posts": len(posts),
        "posts": posts,
    }, ensure_ascii=False, indent=2))


@tool(description=(
    "Get comments on a Facebook post. "
    "post_id: the full post ID in '{page_id}_{post_id}' format (e.g. '123456_789012'). "
    "Returns comment text, author, creation time, and like count. "
    "Requires FACEBOOK_ACCESS_TOKEN environment variable."
))
def facebook_post_comments(
    post_id: str,
    limit: int = 50,
) -> ToolResult:
    """Fetch comments on a Facebook post via Graph API."""
    post_id = post_id.strip()
    if not post_id:
        return ToolResult.error("post_id cannot be empty (format: 'page_id_post_id')")
    limit = max(1, min(limit, 100))

    fields = f"comments.limit({limit}){{id,message,from,created_time,like_count,comment_count}},message,created_time"
    status, data = _get(post_id, {"fields": fields})
    if status == -2:
        return ToolResult.error(data["error"])
    if status == -1:
        return ToolResult.error(data["error"])
    if status == 404:
        return ToolResult.error(f"Post '{post_id}' not found.")
    if status != 200:
        return ToolResult.error(_graph_error(status, data))

    comments_raw = (data.get("comments") or {}).get("data") or []
    comments = []
    for c in comments_raw:
        author = c.get("from", {})
        comments.append({
            "comment_id": c.get("id", ""),
            "text": c.get("message", ""),
            "author": author.get("name", ""),
            "author_id": author.get("id", ""),
            "created_time": _fmt_ts(c.get("created_time", "")),
            "likes": c.get("like_count", 0),
            "replies": c.get("comment_count", 0),
        })

    return ToolResult.ok(json.dumps({
        "post_id": post_id,
        "post_message": (data.get("message") or "")[:200],
        "post_created_time": _fmt_ts(data.get("created_time", "")),
        "fetched_comments": len(comments),
        "comments": comments,
    }, ensure_ascii=False, indent=2))
