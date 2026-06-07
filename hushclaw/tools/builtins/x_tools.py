"""X app connector tools."""
from __future__ import annotations

from hushclaw.secrets import get_secret_store
from hushclaw.tools.base import ToolResult, tool


def _cfg(_config):
    x_cfg = getattr(getattr(_config, "app_connectors", None), "x", None)
    if x_cfg is None:
        raise ValueError("X app connector config is unavailable")
    return x_cfg


@tool(
    name="x_search",
    description=(
        "Search X posts through the connected X API v2 app connector. "
        "Use for X/Twitter post discovery. Returns JSON with posts and sources."
    ),
    timeout=30,
    parallel_safe=True,
)
def x_search(query: str, limit: int = 10, recent: bool = True, _config=None) -> ToolResult:
    from hushclaw.app_connectors.x import search

    return search(_cfg(_config), get_secret_store(), query, limit=limit, recent=recent)


@tool(
    name="x_read_post",
    description="Read a single X post by post id through the connected X API v2 app connector.",
    timeout=30,
    parallel_safe=True,
)
def x_read_post(post_id: str, _config=None) -> ToolResult:
    from hushclaw.app_connectors.x import read_post

    return read_post(_cfg(_config), get_secret_store(), post_id)


@tool(
    name="x_post",
    description=(
        "Create a new X post using the connected X account. "
        "Requires the X connector's allow_actions setting."
    ),
    timeout=30,
    mutating=True,
)
def x_post(text: str, _config=None, _memory_store=None) -> ToolResult:
    from hushclaw.app_connectors.x import post

    return post(_cfg(_config), get_secret_store(), text, memory_store=_memory_store)


@tool(
    name="x_reply",
    description=(
        "Reply to an X post using the connected X account. "
        "Requires the X connector's allow_actions setting."
    ),
    timeout=30,
    mutating=True,
)
def x_reply(post_id: str, text: str, _config=None, _memory_store=None) -> ToolResult:
    from hushclaw.app_connectors.x import reply

    return reply(_cfg(_config), get_secret_store(), post_id=post_id, text=text, memory_store=_memory_store)
