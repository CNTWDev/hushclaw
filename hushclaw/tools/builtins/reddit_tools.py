"""Reddit app connector tools."""
from __future__ import annotations

from hushclaw.secrets import get_secret_store
from hushclaw.tools.base import ToolResult, tool


def _cfg(_config):
    reddit = getattr(getattr(_config, "app_connectors", None), "reddit", None)
    if reddit is None:
        raise ValueError("Reddit app connector config is unavailable")
    return reddit


@tool(
    name="reddit_search",
    description=(
        "Search Reddit posts through the connected Reddit app connector. "
        "Use for subreddit or Reddit-wide post discovery. Returns JSON with summary and sources."
    ),
    timeout=30,
    parallel_safe=True,
)
def reddit_search(query: str, subreddit: str = "", sort: str = "relevance", limit: int = 5, _config=None) -> ToolResult:
    from hushclaw.app_connectors.reddit import search

    return search(_cfg(_config), get_secret_store(), query, subreddit=subreddit, sort=sort, limit=limit)


@tool(
    name="reddit_read",
    description=(
        "Read a Reddit post and top-level comments. Target may be a Reddit URL, post id, or t3_ fullname. "
        "Returns JSON with post content, comments, and sources."
    ),
    timeout=30,
    parallel_safe=True,
)
def reddit_read(target: str, sort: str = "confidence", comment_limit: int = 10, _config=None) -> ToolResult:
    from hushclaw.app_connectors.reddit import read

    return read(_cfg(_config), get_secret_store(), target, sort=sort, comment_limit=comment_limit)


@tool(
    name="reddit_post",
    description=(
        "Create a Reddit post in a subreddit using the connected Reddit account. "
        "Requires the Reddit connector's allow_actions setting."
    ),
    timeout=30,
    mutating=True,
)
def reddit_post(subreddit: str, title: str, body: str = "", url: str = "", _config=None) -> ToolResult:
    from hushclaw.app_connectors.reddit import post

    return post(_cfg(_config), get_secret_store(), subreddit=subreddit, title=title, body=body, url=url)


@tool(
    name="reddit_comment",
    description=(
        "Reply to a Reddit post or comment. Parent must be a Reddit fullname like t3_postid or t1_commentid. "
        "Requires the Reddit connector's allow_actions setting."
    ),
    timeout=30,
    mutating=True,
)
def reddit_comment(parent: str, body: str, _config=None) -> ToolResult:
    from hushclaw.app_connectors.reddit import comment

    return comment(_cfg(_config), get_secret_store(), parent=parent, body=body)
