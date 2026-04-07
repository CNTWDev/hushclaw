"""TikTok tools — two tiers:

Tier 1 (no auth): oEmbed API for basic video metadata.
Tier 2 (Research API): Full video search and comment access.
  Apply at: https://developers.tiktok.com/products/research-api/

Setup for Research API:
  export TIKTOK_CLIENT_KEY="your_client_key"
  export TIKTOK_CLIENT_SECRET="your_client_secret"
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from hushclaw.tools.base import ToolResult, tool

_OEMBED = "https://www.tiktok.com/oembed"
_RESEARCH_BASE = "https://open.tiktokapis.com/v2"
_KEY_ENV = "TIKTOK_CLIENT_KEY"
_SECRET_ENV = "TIKTOK_CLIENT_SECRET"
_TOKEN_CACHE: dict[str, str] = {}  # simple in-process cache: {"token": "...", "expires": ts}

_INSTALL_HINT = (
    "TikTok Research API not configured.\n"
    "1. Apply at https://developers.tiktok.com/products/research-api/\n"
    "2. Once approved, set via Settings → System → Skill API Keys in the WebUI, or:\n"
    "   export TIKTOK_CLIENT_KEY='your_key'\n"
    "   export TIKTOK_CLIENT_SECRET='your_secret'\n"
    "Note: tiktok_video_info() works without any credentials."
)


def _get_research_creds(_config=None) -> tuple[str, str, str | None]:
    """Return (client_key, client_secret, error_message).

    Checks (in priority order):
    1. Environment variables TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET
    2. HushClaw config api_keys (set via Settings → Skill API Keys)
    """
    key = os.environ.get(_KEY_ENV, "").strip()
    secret = os.environ.get(_SECRET_ENV, "").strip()
    if not key or not secret:
        cfg = _config
        if cfg is not None:
            api_keys = getattr(cfg, "api_keys", None) or {}
            key = key or api_keys.get("tiktok_client_key", "").strip()
            secret = secret or api_keys.get("tiktok_client_secret", "").strip()
    if not key or not secret:
        return "", "", _INSTALL_HINT
    return key, secret, None


def _get_research_token(_config=None) -> tuple[str, str | None]:
    """Get a Research API access token (client credentials flow). Returns (token, error)."""
    key, secret, err = _get_research_creds(_config)
    if err:
        return "", err
    if not key or not secret:
        return "", _INSTALL_HINT

    try:
        import httpx
    except ImportError:
        return "", "httpx is not installed. Run: pip install httpx"

    try:
        resp = httpx.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": key,
                "client_secret": secret,
                "grant_type": "client_credentials",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        d = resp.json()
        if resp.status_code != 200 or "access_token" not in d:
            return "", f"Token request failed ({resp.status_code}): {d.get('error_description') or d}"
        return d["access_token"], None
    except Exception as e:
        return "", f"Failed to get TikTok access token: {e}"


def _research_post(path: str, body: dict, _config=None) -> tuple[int, dict]:
    """POST to TikTok Research API."""
    try:
        import httpx
    except ImportError:
        return -1, {"error": "httpx is not installed. Run: pip install httpx"}

    token, err = _get_research_token(_config)
    if err:
        return -2, {"error": err}

    try:
        resp = httpx.post(
            f"{_RESEARCH_BASE}{path}",
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=20,
        )
        return resp.status_code, resp.json()
    except httpx.RequestError as e:
        return -1, {"error": f"Network error: {e}"}


def _fmt_ts(ts: int | str) -> str:
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)


@tool(description=(
    "Get basic metadata for a TikTok video by URL. "
    "No authentication required — uses TikTok's public oEmbed endpoint. "
    "Returns title, author, and thumbnail info."
))
def tiktok_video_info(video_url: str) -> ToolResult:
    """Fetch TikTok video metadata via oEmbed (no credentials needed)."""
    if not video_url.strip():
        return ToolResult.error("video_url cannot be empty")
    if "tiktok.com" not in video_url:
        return ToolResult.error("URL must be a TikTok video URL (e.g. https://www.tiktok.com/@user/video/123)")

    try:
        import httpx
    except ImportError:
        return ToolResult.error("httpx is not installed. Run: pip install httpx")

    try:
        resp = httpx.get(
            _OEMBED,
            params={"url": video_url},
            headers={"User-Agent": "HushClaw-SocialInsights/1.0"},
            timeout=15,
            follow_redirects=True,
        )
        if resp.status_code == 404:
            return ToolResult.error("Video not found or may have been deleted.")
        if resp.status_code != 200:
            return ToolResult.error(f"oEmbed request failed: HTTP {resp.status_code}")
        d = resp.json()
    except httpx.RequestError as e:
        return ToolResult.error(f"Network error: {e}")
    except Exception as e:
        return ToolResult.error(f"Failed to fetch TikTok video info: {e}")

    return ToolResult.ok(json.dumps({
        "title": d.get("title", ""),
        "author_name": d.get("author_name", ""),
        "author_url": d.get("author_url", ""),
        "thumbnail_url": d.get("thumbnail_url", ""),
        "thumbnail_width": d.get("thumbnail_width"),
        "thumbnail_height": d.get("thumbnail_height"),
        "provider_name": d.get("provider_name", "TikTok"),
        "video_url": video_url,
    }, ensure_ascii=False, indent=2))


@tool(description=(
    "Search TikTok videos by keyword/topic using the TikTok Research API. "
    "query (required): the search keyword or topic. "
    "start_date / end_date: format YYYYMMDD (default: last 7 days if omitted). "
    "limit: max videos to return (default 10, max 100). "
    "Requires TIKTOK_CLIENT_KEY + TIKTOK_CLIENT_SECRET environment variables."
))
def tiktok_search(
    query: str,
    start_date: str = "",
    end_date: str = "",
    limit: int = 10,
    _config=None,
) -> ToolResult:
    """Search TikTok videos via Research API. `query` is the required keyword/topic."""
    if not query.strip():
        return ToolResult.error("query cannot be empty — provide a search keyword or topic")
    limit = max(1, min(limit, 100))

    # Default date range: last 7 days
    if not start_date or not end_date:
        from datetime import timedelta
        today = datetime.now(tz=timezone.utc)
        end_date = end_date or today.strftime("%Y%m%d")
        start_date = start_date or (today - timedelta(days=7)).strftime("%Y%m%d")

    # API body: field_name is "keyword" (TikTok Research API term), query is the user's search term
    body = {
        "query": {
            "and": [
                {"operation": "IN", "field_name": "keyword", "field_values": [query]}
            ]
        },
        "start_date": start_date,
        "end_date": end_date,
        "max_count": limit,
        "fields": "id,desc,create_time,share_count,view_count,like_count,comment_count,author_name,hashtag_names",
    }

    status, data = _research_post("/research/video/query/", body, _config)
    if status == -2:
        return ToolResult.error(data["error"])
    if status == -1:
        return ToolResult.error(data["error"])
    if status != 200:
        err = data.get("error", {})
        return ToolResult.error(
            f"TikTok Research API error {status}: {err.get('message') or err}"
        )

    videos_raw = (data.get("data") or {}).get("videos") or []
    videos = []
    for v in videos_raw:
        videos.append({
            "video_id": str(v.get("id", "")),
            "description": v.get("desc", ""),
            "author": v.get("author_name", ""),
            "created_at": _fmt_ts(v.get("create_time", 0)),
            "views": v.get("view_count", 0),
            "likes": v.get("like_count", 0),
            "comments": v.get("comment_count", 0),
            "shares": v.get("share_count", 0),
            "hashtags": v.get("hashtag_names", []),
            "url": f"https://www.tiktok.com/video/{v.get('id', '')}",
        })

    return ToolResult.ok(json.dumps({
        "query": query,
        "date_range": f"{start_date} ~ {end_date}",
        "count": len(videos),
        "videos": videos,
    }, ensure_ascii=False, indent=2))


@tool(description=(
    "Get comments for a TikTok video by video ID (numeric ID from the video URL). "
    "Requires TIKTOK_CLIENT_KEY + TIKTOK_CLIENT_SECRET environment variables."
))
def tiktok_video_comments(
    video_id: str,
    limit: int = 50,
    _config=None,
) -> ToolResult:
    """Fetch comments on a TikTok video via Research API."""
    video_id = video_id.strip()
    if not video_id:
        return ToolResult.error("video_id cannot be empty (numeric ID from the video URL)")
    limit = max(1, min(limit, 100))

    body = {
        "video_id": int(video_id),
        "max_count": limit,
        "fields": "id,text,like_count,create_time,parent_comment_id",
    }

    status, data = _research_post("/research/video/comment/list/", body, _config)
    if status == -2:
        return ToolResult.error(data["error"])
    if status == -1:
        return ToolResult.error(data["error"])
    if status != 200:
        err = data.get("error", {})
        return ToolResult.error(
            f"TikTok Research API error {status}: {err.get('message') or err}"
        )

    comments_raw = (data.get("data") or {}).get("comments") or []
    comments = []
    for c in comments_raw:
        comments.append({
            "id": str(c.get("id", "")),
            "text": c.get("text", ""),
            "likes": c.get("like_count", 0),
            "created_at": _fmt_ts(c.get("create_time", 0)),
            "is_reply": bool(c.get("parent_comment_id")),
        })

    return ToolResult.ok(json.dumps({
        "video_id": video_id,
        "fetched_comments": len(comments),
        "comments": comments,
    }, ensure_ascii=False, indent=2))
