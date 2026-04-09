"""Twitter / X tools — uses the Twitter API v2 with a Bearer Token.

Setup: set the environment variable TWITTER_BEARER_TOKEN.
  export TWITTER_BEARER_TOKEN="AAA..."

Free tier (as of 2025):
  - Read-only access to recent tweets (last 7 days)
  - ~1 million tweet reads / month
  - Obtain a Bearer Token at: https://developer.x.com/en/portal/dashboard
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from hushclaw.tools.base import ToolResult, tool

_BASE = "https://api.twitter.com/2"
_TOKEN_ENV = "TWITTER_BEARER_TOKEN"
_INSTALL_HINT = (
    "Twitter Bearer Token not configured.\n"
    "1. Create a free developer account at https://developer.x.com\n"
    "2. Create a project/app and copy the Bearer Token\n"
    "3. Set it: export TWITTER_BEARER_TOKEN='your_token_here'"
)


def _headers() -> dict[str, str] | None:
    token = os.environ.get(_TOKEN_ENV, "").strip()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def _get(path: str, params: dict) -> tuple[int, dict]:
    """Perform a GET against the Twitter v2 API using httpx."""
    try:
        import httpx
    except ImportError:
        return -1, {"error": "httpx is not installed. Run: pip install httpx"}

    hdrs = _headers()
    if hdrs is None:
        return -2, {"error": _INSTALL_HINT}

    url = f"{_BASE}{path}"
    try:
        resp = httpx.get(url, params=params, headers=hdrs, timeout=15)
        return resp.status_code, resp.json()
    except httpx.RequestError as e:
        return -1, {"error": f"Network error: {e}"}


def _fmt_ts(s: str) -> str:
    """Convert ISO 8601 Twitter timestamp to readable format."""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return s


def _tweet_to_dict(tweet: dict) -> dict:
    metrics = tweet.get("public_metrics", {})
    return {
        "id": tweet.get("id", ""),
        "text": tweet.get("text", ""),
        "created_at": _fmt_ts(tweet.get("created_at", "")),
        "likes": metrics.get("like_count", 0),
        "retweets": metrics.get("retweet_count", 0),
        "replies": metrics.get("reply_count", 0),
        "quotes": metrics.get("quote_count", 0),
        "url": f"https://x.com/i/web/status/{tweet.get('id', '')}",
    }


@tool(description=(
    "Search recent tweets (last 7 days) on X/Twitter by keyword or operator query. "
    "Supports Twitter query operators: from:user, to:user, #hashtag, -exclusion, lang:en, etc. "
    "Requires TWITTER_BEARER_TOKEN environment variable."
))
def twitter_search(
    query: str,
    limit: int = 10,
) -> ToolResult:
    """Search recent tweets using Twitter API v2."""
    if not query.strip():
        return ToolResult.error("query cannot be empty")
    limit = max(1, min(limit, 100))

    params = {
        "query": query,
        "max_results": limit,
        "tweet.fields": "created_at,public_metrics,author_id",
        "expansions": "author_id",
        "user.fields": "username,name",
    }
    status, data = _get("/tweets/search/recent", params)

    if status == -2:
        return ToolResult.error(data["error"])
    if status == -1:
        return ToolResult.error(data["error"])
    if status == 401:
        return ToolResult.error("Authentication failed. Check your TWITTER_BEARER_TOKEN.")
    if status == 403:
        return ToolResult.error(
            "Access forbidden. Your API access level may not include search. "
            "Ensure you have at least Basic access at developer.x.com."
        )
    if status != 200:
        errs = data.get("errors") or data.get("detail") or data
        return ToolResult.error(f"Twitter API error {status}: {errs}")

    # Build username lookup from includes
    users_by_id: dict[str, dict] = {}
    for u in (data.get("includes") or {}).get("users", []):
        users_by_id[u["id"]] = u

    tweets = []
    for t in (data.get("data") or []):
        entry = _tweet_to_dict(t)
        author = users_by_id.get(t.get("author_id", ""), {})
        entry["author"] = author.get("username", "")
        entry["author_name"] = author.get("name", "")
        tweets.append(entry)

    meta = data.get("meta", {})
    return ToolResult.ok(json.dumps({
        "query": query,
        "result_count": meta.get("result_count", len(tweets)),
        "newest_id": meta.get("newest_id", ""),
        "oldest_id": meta.get("oldest_id", ""),
        "tweets": tweets,
    }, ensure_ascii=False, indent=2))


@tool(description=(
    "Get the most recent tweets from a specific X/Twitter user by username (without @). "
    "Requires TWITTER_BEARER_TOKEN environment variable."
))
def twitter_user_tweets(
    username: str,
    limit: int = 10,
) -> ToolResult:
    """Fetch recent tweets for a given Twitter username."""
    username = username.lstrip("@").strip()
    if not username:
        return ToolResult.error("username cannot be empty")
    limit = max(1, min(limit, 100))

    # Step 1: resolve username → user_id
    status, data = _get("/users/by/username/" + username, {"user.fields": "name,description,public_metrics"})
    if status == -2:
        return ToolResult.error(data["error"])
    if status == -1:
        return ToolResult.error(data["error"])
    if status == 404:
        return ToolResult.error(f"User @{username} not found")
    if status != 200:
        return ToolResult.error(f"Failed to look up user: {data}")

    user = data.get("data", {})
    user_id = user.get("id", "")
    user_metrics = user.get("public_metrics", {})

    # Step 2: fetch tweets
    status2, tweets_data = _get(f"/users/{user_id}/tweets", {
        "max_results": limit,
        "tweet.fields": "created_at,public_metrics",
        "exclude": "retweets,replies",
    })
    if status2 != 200:
        errs = tweets_data.get("errors") or tweets_data.get("detail") or tweets_data
        return ToolResult.error(f"Failed to fetch tweets: {errs}")

    tweets = [_tweet_to_dict(t) for t in (tweets_data.get("data") or [])]

    return ToolResult.ok(json.dumps({
        "username": username,
        "user_id": user_id,
        "display_name": user.get("name", ""),
        "followers": user_metrics.get("followers_count", 0),
        "following": user_metrics.get("following_count", 0),
        "tweet_count": user_metrics.get("tweet_count", 0),
        "fetched": len(tweets),
        "tweets": tweets,
    }, ensure_ascii=False, indent=2))
