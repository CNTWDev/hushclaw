"""Memory tools: remember, recall, search_notes."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ghostclaw.tools.base import tool, ToolResult

if TYPE_CHECKING:
    from ghostclaw.memory.store import MemoryStore


@tool(
    name="remember",
    description="Save important information to persistent memory for future sessions.",
)
def remember(
    content: str,
    title: str = "",
    tags: list = None,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    """Save a note to persistent memory."""
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    note_id = _memory_store.remember(content, title=title, tags=tags or [])
    return ToolResult.ok(f"Saved to memory (id={note_id[:8]})")


@tool(
    name="recall",
    description="Search persistent memory and retrieve relevant information.",
)
def recall(
    query: str,
    limit: int = 5,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    """Search and return relevant memories."""
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    text = _memory_store.recall(query, limit=limit)
    return ToolResult.ok(text)


@tool(
    name="search_notes",
    description="Search notes by keyword or phrase and return matching titles and snippets.",
)
def search_notes(
    query: str,
    limit: int = 5,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    """Search notes and return structured results."""
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    results = _memory_store.search(query, limit=limit)
    if not results:
        return ToolResult.ok("No notes found matching your query.")
    lines = []
    for r in results:
        snippet = r["body"][:150].replace("\n", " ")
        lines.append(f"• [{r['note_id'][:8]}] {r['title']}: {snippet}...")
    return ToolResult.ok("\n".join(lines))
