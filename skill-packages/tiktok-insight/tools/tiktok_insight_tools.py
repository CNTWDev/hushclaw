"""TikTok Insight tools — market intelligence via Scrape Creators API.

Tools:
  tiktok_search_videos      — keyword search with structured engagement summary
  tiktok_compare_keywords   — side-by-side keyword opportunity comparison
  tiktok_get_video_info     — detailed stats for a single video
  tiktok_get_video_comments — comments with basic sentiment bucketing
  tiktok_get_user_profile   — creator profile (followers, engagement baseline)
  tiktok_get_popular        — trending videos / hashtags / creators / songs

Setup: SCRAPE_CREATORS_API_KEY via Settings → System → Skill API Keys,
       or: export SCRAPE_CREATORS_API_KEY='your_key'
       Free key: https://scrapecreators.com
"""
from __future__ import annotations

import json
import os

import requests

from hushclaw.tools.base import ToolResult, tool

_BASE = "https://api.scrapecreators.com/v1/tiktok"
_INSTALL_HINT = (
    "SCRAPE_CREATORS_API_KEY not set.\n"
    "Set it via Settings → System → Skill API Keys in the WebUI, or run:\n"
    "  export SCRAPE_CREATORS_API_KEY='your_key'\n"
    "Get a free key at https://scrapecreators.com"
)


def _get_api_key() -> tuple[str, str | None]:
    key = os.environ.get("SCRAPE_CREATORS_API_KEY", "").strip()
    if not key:
        return "", _INSTALL_HINT
    return key, None


def _headers(key: str) -> dict:
    return {"x-api-key": key}


def _engagement_rate(v: int, l: int, c: int, s: int) -> float:
    """(likes + comments + shares) / views, returns percentage."""
    if not v:
        return 0.0
    return round((l + c + s) / v * 100, 2)


def _rate_label(rate: float) -> str:
    if rate > 10:
        return "viral"
    if rate > 5:
        return "high"
    if rate > 2:
        return "average"
    return "low"


def _summarize_videos(raw_videos: list) -> dict:
    """Transform raw API video list into a structured intelligence summary."""
    if not raw_videos:
        return {"count": 0, "videos": [], "summary": {}}

    processed = []
    hashtag_freq: dict[str, int] = {}

    for v in raw_videos:
        stats = v.get("stats") or v.get("statsV2") or {}
        # Normalise field names across API versions
        plays   = int(stats.get("playCount") or stats.get("play_count") or v.get("play_count") or 0)
        likes   = int(stats.get("diggCount") or stats.get("like_count") or v.get("digg_count") or 0)
        comments = int(stats.get("commentCount") or stats.get("comment_count") or v.get("comment_count") or 0)
        shares  = int(stats.get("shareCount") or stats.get("share_count") or v.get("share_count") or 0)

        eng_rate = _engagement_rate(plays, likes, comments, shares)

        author = v.get("author") or {}
        author_name = (
            author.get("uniqueId") or author.get("nickname") or
            v.get("author_name") or v.get("authorMeta", {}).get("name") or ""
        )
        author_fans = int(
            author.get("fans") or author.get("followerCount") or
            v.get("authorMeta", {}).get("fans") or 0
        )

        desc = v.get("desc") or v.get("description") or ""
        video_id = str(v.get("id") or v.get("video_id") or "")
        url = (
            v.get("webVideoUrl") or
            v.get("url") or
            (f"https://www.tiktok.com/@{author_name}/video/{video_id}" if video_id and author_name else "")
        )

        # Collect hashtags
        tags = []
        for ht in (v.get("challenges") or v.get("hashtags") or []):
            tag = ht.get("title") or ht.get("name") or ""
            if tag:
                tags.append(tag)
                hashtag_freq[tag] = hashtag_freq.get(tag, 0) + 1

        processed.append({
            "video_id": video_id,
            "url": url,
            "description": desc[:200],
            "author": author_name,
            "author_followers": author_fans,
            "plays": plays,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "engagement_rate_pct": eng_rate,
            "engagement_level": _rate_label(eng_rate),
            "hashtags": tags,
        })

    # Sort by engagement rate for the summary ranking
    processed.sort(key=lambda x: x["engagement_rate_pct"], reverse=True)

    # Aggregate
    total_plays = sum(v["plays"] for v in processed)
    avg_eng = round(sum(v["engagement_rate_pct"] for v in processed) / len(processed), 2)
    top_tags = sorted(hashtag_freq.items(), key=lambda x: -x[1])[:10]

    # Determine creator size distribution
    small_creators = sum(1 for v in processed if 0 < v["author_followers"] < 100_000)
    competition_signal = "low" if small_creators > len(processed) * 0.6 else "high"

    return {
        "count": len(processed),
        "videos": processed,
        "summary": {
            "total_plays_in_results": total_plays,
            "avg_engagement_rate_pct": avg_eng,
            "avg_engagement_level": _rate_label(avg_eng),
            "trending_hashtags": [{"tag": t, "freq": f} for t, f in top_tags],
            "competition_signal": competition_signal,
            "analysis_note": (
                f"Top video engagement: {processed[0]['engagement_rate_pct']}% "
                f"({processed[0]['engagement_level']}). "
                f"Small-creator dominance: {'yes' if competition_signal == 'low' else 'no'} "
                f"({small_creators}/{len(processed)} videos from accounts <100K followers)."
            ) if processed else "",
        },
    }


@tool(description=(
    "Search TikTok videos by keyword/topic. Returns a structured market intelligence summary "
    "including engagement rates, trending hashtags, competition signal, and ranked video list. "
    "query (required): search keyword or topic. "
    "cursor: pagination cursor from previous response (default 0)."
))
def tiktok_search_videos(query: str, cursor: int = 0) -> ToolResult:
    """Search TikTok videos with structured engagement summary."""
    if not query.strip():
        return ToolResult.error("query cannot be empty — provide a search keyword or topic")
    api_key, err = _get_api_key()
    if err:
        return ToolResult.error(err)
    try:
        resp = requests.get(
            f"{_BASE}/search/keyword",
            headers=_headers(api_key),
            params={"query": query, "cursor": cursor},
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        return ToolResult.error(f"Search failed: {e}")

    # API may return videos in different shapes
    raw_videos = (
        raw.get("videoList") or raw.get("videos") or
        raw.get("data", {}).get("videos") or
        (raw if isinstance(raw, list) else [])
    )

    result = _summarize_videos(raw_videos)
    result["query"] = query
    result["cursor"] = cursor
    result["has_more"] = bool(raw.get("hasMore") or raw.get("has_more"))
    return ToolResult.ok(json.dumps(result, ensure_ascii=False, indent=2))


@tool(description=(
    "Compare 2–5 TikTok keywords side-by-side to identify the best market opportunity. "
    "queries (required): list of keywords, e.g. ['budget phone', 'cheap smartphone', 'affordable mobile']. "
    "Returns market size, competition level, avg engagement, and an opportunity score for each keyword."
))
def tiktok_compare_keywords(queries: list) -> ToolResult:
    """Multi-keyword comparison for market opportunity analysis."""
    if not queries:
        return ToolResult.error("queries cannot be empty — provide a list of 2–5 keywords to compare")
    if isinstance(queries, str):
        # Tolerate comma-separated string input
        queries = [q.strip() for q in queries.split(",") if q.strip()]
    if len(queries) < 2:
        return ToolResult.error("Provide at least 2 keywords to compare (e.g. ['keyword A', 'keyword B'])")
    if len(queries) > 5:
        queries = queries[:5]

    api_key, err = _get_api_key()
    if err:
        return ToolResult.error(err)

    comparison = []
    for kw in queries:
        kw = str(kw).strip()
        if not kw:
            continue
        try:
            resp = requests.get(
                f"{_BASE}/search/keyword",
                headers=_headers(api_key),
                params={"query": kw, "cursor": 0},
                timeout=30,
            )
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:
            comparison.append({"keyword": kw, "error": str(e)})
            continue

        raw_videos = (
            raw.get("videoList") or raw.get("videos") or
            raw.get("data", {}).get("videos") or
            (raw if isinstance(raw, list) else [])
        )
        s = _summarize_videos(raw_videos)
        summary = s["summary"]
        count = s["count"]

        # Opportunity score: high demand + low competition + high engagement = good
        # Demand proxy: result count; Competition proxy: creator size signal; Quality proxy: avg engagement
        demand_score = min(count / 10, 10)  # up to 10 pts
        competition_penalty = 0 if summary.get("competition_signal") == "low" else -3
        engagement_score = min(summary.get("avg_engagement_rate_pct", 0), 10)
        opportunity = round(demand_score + competition_penalty + engagement_score, 1)

        comparison.append({
            "keyword": kw,
            "result_count": count,
            "avg_engagement_rate_pct": summary.get("avg_engagement_rate_pct", 0),
            "engagement_level": summary.get("avg_engagement_level", "n/a"),
            "competition_signal": summary.get("competition_signal", "n/a"),
            "top_hashtags": [t["tag"] for t in (summary.get("trending_hashtags") or [])[:5]],
            "opportunity_score": opportunity,
            "opportunity_verdict": (
                "★★★ Best opportunity" if opportunity >= 12 else
                "★★  Good opportunity" if opportunity >= 8 else
                "★   Moderate" if opportunity >= 5 else
                "✗   Low opportunity"
            ),
        })

    comparison.sort(key=lambda x: x.get("opportunity_score", 0), reverse=True)
    winner = comparison[0] if comparison else {}
    return ToolResult.ok(json.dumps({
        "comparison": comparison,
        "recommendation": (
            f"Best keyword: '{winner.get('keyword')}' "
            f"(opportunity score {winner.get('opportunity_score')}, "
            f"{winner.get('engagement_level')} engagement, "
            f"{winner.get('competition_signal')} competition)"
        ) if winner else "No data",
    }, ensure_ascii=False, indent=2))


@tool(description=(
    "Get detailed stats (plays, likes, shares, comments, author info) for a specific TikTok video. "
    "video_url (required): full TikTok video URL."
))
def tiktok_get_video_info(video_url: str) -> ToolResult:
    """Detailed stats for a single TikTok video."""
    if not video_url.strip():
        return ToolResult.error("video_url cannot be empty — provide a full TikTok video URL")
    api_key, err = _get_api_key()
    if err:
        return ToolResult.error(err)
    try:
        resp = requests.get(
            f"{_BASE}/video/info",
            headers=_headers(api_key),
            params={"url": video_url},
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        return ToolResult.error(f"Get video info failed: {e}")

    # Enrich with computed engagement rate
    item = raw.get("itemInfo", {}).get("itemStruct") or raw
    stats = item.get("stats") or item.get("statsV2") or {}
    plays    = int(stats.get("playCount") or 0)
    likes    = int(stats.get("diggCount") or 0)
    comments = int(stats.get("commentCount") or 0)
    shares   = int(stats.get("shareCount") or 0)
    if plays:
        raw["_computed"] = {
            "engagement_rate_pct": _engagement_rate(plays, likes, comments, shares),
            "engagement_level": _rate_label(_engagement_rate(plays, likes, comments, shares)),
            "comment_rate_pct": round(comments / plays * 100, 3) if plays else 0,
            "share_rate_pct": round(shares / plays * 100, 3) if plays else 0,
        }
    return ToolResult.ok(json.dumps(raw, ensure_ascii=False, indent=2))


@tool(description=(
    "Get user comments for a TikTok video with basic sentiment bucketing. "
    "Returns raw comments plus a quick count of positive/negative/question/intent signals. "
    "video_url (required): full TikTok video URL. "
    "cursor: pagination cursor (default 0)."
))
def tiktok_get_video_comments(video_url: str, cursor: int = 0) -> ToolResult:
    """Comments with basic sentiment bucketing for quicker analysis."""
    if not video_url.strip():
        return ToolResult.error("video_url cannot be empty — provide a full TikTok video URL")
    api_key, err = _get_api_key()
    if err:
        return ToolResult.error(err)
    try:
        resp = requests.get(
            f"{_BASE}/video/comments",
            headers=_headers(api_key),
            params={"url": video_url, "cursor": cursor},
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        return ToolResult.error(f"Get comments failed: {e}")

    # Quick sentiment bucketing based on keyword signals
    comments_list = (
        raw.get("comments") or raw.get("data", {}).get("comments") or
        (raw if isinstance(raw, list) else [])
    )

    _positive_kw = {"love", "great", "amazing", "perfect", "best", "good", "nice", "awesome",
                    "excellent", "fantastic", "bought", "worth", "recommend", "5 star", "❤", "🔥", "👍", "💯"}
    _negative_kw = {"bad", "worst", "broke", "broken", "fake", "scam", "waste", "disappointed",
                    "return", "refund", "poor quality", "cheap", "不好", "差", "坏", "假", "垃圾", "👎"}
    _intent_kw   = {"where to buy", "how much", "price", "link", "where can i", "which one",
                    "available", "in stock", "shipping", "多少钱", "在哪买", "链接", "有货吗"}
    _question_kw = {"?", "？", "how", "what", "why", "when", "which", "who", "怎么", "什么", "为什么", "哪里"}

    buckets = {"positive": 0, "negative": 0, "purchase_intent": 0, "questions": 0, "neutral": 0}
    for c in comments_list:
        text = (c.get("text") or c.get("comment_text") or "").lower()
        if not text:
            continue
        matched = False
        if any(k in text for k in _intent_kw):
            buckets["purchase_intent"] += 1
            matched = True
        if any(k in text for k in _negative_kw):
            buckets["negative"] += 1
            matched = True
        if any(k in text for k in _positive_kw):
            buckets["positive"] += 1
            matched = True
        if not matched and any(k in text for k in _question_kw):
            buckets["questions"] += 1
            matched = True
        if not matched:
            buckets["neutral"] += 1

    result = dict(raw)
    result["_sentiment_buckets"] = buckets
    result["_sentiment_note"] = (
        f"Scanned {len(comments_list)} comments: "
        f"{buckets['positive']} positive, {buckets['negative']} negative, "
        f"{buckets['purchase_intent']} purchase-intent, {buckets['questions']} questions."
    )
    return ToolResult.ok(json.dumps(result, ensure_ascii=False, indent=2))


@tool(description=(
    "Get a TikTok creator's profile: follower count, total likes, bio, and recent content overview. "
    "username (required): TikTok handle (with or without @)."
))
def tiktok_get_user_profile(username: str) -> ToolResult:
    """Creator profile for benchmarking and competitor analysis."""
    if not username.strip():
        return ToolResult.error("username cannot be empty — provide a TikTok handle (e.g. @creator or creator)")
    username = username.lstrip("@").strip()
    api_key, err = _get_api_key()
    if err:
        return ToolResult.error(err)
    try:
        resp = requests.get(
            f"{_BASE}/user/info",
            headers=_headers(api_key),
            params={"username": username},
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        return ToolResult.error(f"Get user profile failed: {e}")
    return ToolResult.ok(json.dumps(raw, ensure_ascii=False, indent=2))


@tool(description=(
    "Get what's trending on TikTok right now. "
    "type: 'videos' (default) | 'hashtags' | 'creators' | 'songs'."
))
def tiktok_get_popular(type: str = "videos") -> ToolResult:
    """Trending content, hashtags, creators, or songs."""
    api_key, err = _get_api_key()
    if err:
        return ToolResult.error(err)
    endpoint_map = {
        "videos":   f"{_BASE}/popular/videos",
        "creators": f"{_BASE}/popular/creators",
        "hashtags": f"{_BASE}/popular/hashtags",
        "songs":    f"{_BASE}/popular/songs",
    }
    url = endpoint_map.get(type, endpoint_map["videos"])
    try:
        resp = requests.get(url, headers=_headers(api_key), timeout=30)
        if resp.status_code == 404:
            return ToolResult.error(
                f"Popular/{type} endpoint not found. "
                "Try tiktok_search_videos with a trending topic instead."
            )
        resp.raise_for_status()
        return ToolResult.ok(resp.text)
    except Exception as e:
        return ToolResult.error(f"Failed to get popular {type}: {e}")
