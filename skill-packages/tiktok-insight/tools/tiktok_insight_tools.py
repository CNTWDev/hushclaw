from __future__ import annotations
import os
import requests
from hushclaw.tools.base import ToolResult, tool

_INSTALL_HINT = (
    "SCRAPE_CREATORS_API_KEY not set.\n"
    "Get a free key at https://scrapecreators.com and run:\n"
    "  export SCRAPE_CREATORS_API_KEY='your_key'"
)


def _get_api_key() -> tuple[str, str | None]:
    """Return (api_key, error_message). error_message is set when key is missing."""
    key = os.environ.get("SCRAPE_CREATORS_API_KEY", "").strip()
    if not key:
        return "", _INSTALL_HINT
    return key, None

@tool(description=(
    "Search TikTok videos by keyword/topic. "
    "query (required): the search keyword or topic string. "
    "cursor: pagination cursor from a previous response (default 0). "
    "Returns a list of matching videos with play counts, like counts, author info, etc."
))
def tiktok_search_videos(query: str, cursor: int = 0) -> ToolResult:
    """Search TikTok videos. `query` is the required keyword/topic."""
    if not query.strip():
        return ToolResult.error("query cannot be empty — provide a search keyword or topic")
    api_key, err = _get_api_key()
    if err:
        return ToolResult.error(err)
    url = "https://api.scrapecreators.com/v1/tiktok/search/keyword"
    headers = {"x-api-key": api_key}
    params = {"query": query, "cursor": cursor}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        return ToolResult(content=response.text)
    except Exception as e:
        return ToolResult(content=f"Search failed: {str(e)}", is_error=True)

@tool(description="Get detailed info (plays, likes, shares, author) for a TikTok video. video_url (required): full TikTok video URL.")
def tiktok_get_video_info(video_url: str) -> ToolResult:
    if not video_url.strip():
        return ToolResult.error("video_url cannot be empty — provide a full TikTok video URL")
    api_key, err = _get_api_key()
    if err:
        return ToolResult.error(err)
    url = "https://api.scrapecreators.com/v1/tiktok/video/info"
    headers = {"x-api-key": api_key}
    params = {"url": video_url}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        return ToolResult(content=response.text)
    except Exception as e:
        return ToolResult(content=f"Get video info failed: {str(e)}", is_error=True)

@tool(description="Get user comments for a TikTok video. video_url (required): full TikTok video URL. cursor: pagination cursor (default 0).")
def tiktok_get_video_comments(video_url: str, cursor: int = 0) -> ToolResult:
    if not video_url.strip():
        return ToolResult.error("video_url cannot be empty — provide a full TikTok video URL")
    api_key, err = _get_api_key()
    if err:
        return ToolResult.error(err)
    url = "https://api.scrapecreators.com/v1/tiktok/video/comments"
    headers = {"x-api-key": api_key}
    params = {"url": video_url, "cursor": cursor}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        return ToolResult(content=response.text)
    except Exception as e:
        return ToolResult(content=f"Get comments failed: {str(e)}", is_error=True)

@tool(description=(
    "Get popular creators/videos/hashtags/songs on TikTok. "
    "type: one of 'videos', 'creators', 'hashtags', 'songs' (default: 'videos')."
))
def tiktok_get_popular(type: str = "videos") -> ToolResult:
    api_key, err = _get_api_key()
    if err:
        return ToolResult.error(err)
    endpoint_map = {
        "videos": "https://api.scrapecreators.com/v1/tiktok/popular/videos",
        "creators": "https://api.scrapecreators.com/v1/tiktok/popular/creators",
        "hashtags": "https://api.scrapecreators.com/v1/tiktok/popular/hashtags",
        "songs": "https://api.scrapecreators.com/v1/tiktok/popular/songs",
    }
    url = endpoint_map.get(type, endpoint_map["videos"])
    headers = {"x-api-key": api_key}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 404:
            return ToolResult(content="Popular endpoint 404. Try tiktok_search_videos with 'trending' or specific tags.", is_error=True)
        response.raise_for_status()
        return ToolResult(content=response.text)
    except Exception as e:
        return ToolResult(content=f"Failed to get popular {type}: {str(e)}", is_error=True)
