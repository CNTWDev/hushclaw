"""Session history tools."""
from __future__ import annotations

import json
from typing import Any

from hushclaw.tools.base import ToolResult, tool


def _clip(text: str, limit: int = 520) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 14)].rstrip() + " [truncated]"


@tool(
    name="session_search",
    description=(
        "Search prior conversation sessions using deterministic local FTS. "
        "Use mode='discovery' to find sessions by query, mode='browse' with "
        "session_id to inspect turns, or mode='scroll' with cursor to continue."
    ),
    parallel_safe=True,
)
def session_search(
    query: str = "",
    mode: str = "discovery",
    session_id: str = "",
    cursor: str = "",
    limit: int = 5,
    _memory_store=None,
) -> ToolResult:
    """Search or browse prior session evidence without using an auxiliary LLM."""
    if _memory_store is None:
        return ToolResult.error("session_search requires a memory store")

    mode = (mode or "discovery").strip().lower()
    limit = max(1, min(int(limit or 5), 20))
    if mode not in {"discovery", "browse", "scroll"}:
        return ToolResult.error("mode must be one of: discovery, browse, scroll")

    if mode == "discovery":
        q = (query or "").strip()
        if not q:
            return ToolResult.error("query is required for discovery mode")
        search = getattr(_memory_store, "search_sessions", None)
        if search is None:
            return ToolResult.error("memory store does not support session search")
        rows = list(search(q, limit=limit, include_scheduled=False) or [])
        items = []
        lines = [f"Session search results for {q!r}:"]
        for idx, row in enumerate(rows, start=1):
            item = {
                "session_id": str(row.get("session_id") or ""),
                "turn_id": str(row.get("turn_id") or ""),
                "role": str(row.get("role") or ""),
                "title": str(row.get("title") or ""),
                "ts": row.get("ts"),
                "snippet": str(row.get("snippet") or row.get("content") or ""),
                "score": row.get("score"),
            }
            items.append(item)
            lines.append(
                f"{idx}. session={item['session_id']} turn={item['turn_id']} "
                f"role={item['role']} title={item['title']!r}\n"
                f"   {_clip(item['snippet'])}"
            )
        if not items:
            lines.append("No matching prior sessions found.")
        return ToolResult(
            content="\n".join(lines),
            metadata={"mode": "discovery", "query": q, "items": items},
        )

    if mode == "browse":
        sid = (session_id or "").strip()
        if not sid:
            return ToolResult.error("session_id is required for browse mode")
        offset = 0
    else:
        try:
            payload = json.loads(cursor or "{}")
        except Exception:
            return ToolResult.error("cursor must be a JSON string returned by session_search")
        sid = str(payload.get("session_id") or session_id or "").strip()
        offset = max(0, int(payload.get("offset") or 0))
        if not sid:
            return ToolResult.error("cursor/session_id is required for scroll mode")

    turns = list(getattr(_memory_store, "load_session_history")(sid) or [])
    window = turns[offset : offset + limit]
    next_offset = offset + len(window)
    has_more = next_offset < len(turns)
    next_cursor = json.dumps({"session_id": sid, "offset": next_offset}, ensure_ascii=False) if has_more else ""
    lines = [f"Session {sid} turns {offset + 1}-{next_offset} of {len(turns)}:"]
    items: list[dict[str, Any]] = []
    for idx, turn in enumerate(window, start=offset + 1):
        item = {
            "index": idx,
            "turn_id": str(turn.get("turn_id") or ""),
            "role": str(turn.get("role") or ""),
            "ts": turn.get("ts"),
            "content": str(turn.get("content") or ""),
        }
        items.append(item)
        lines.append(f"{idx}. role={item['role']} turn={item['turn_id']}\n   {_clip(item['content'], 900)}")
    if has_more:
        lines.append(f"More turns available. Call session_search(mode='scroll', cursor={next_cursor!r}).")
    if not window:
        lines.append("No turns found for this session.")
    return ToolResult(
        content="\n".join(lines),
        metadata={
            "mode": mode,
            "session_id": sid,
            "offset": offset,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "items": items,
        },
    )
