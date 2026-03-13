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


@tool(
    name="remember_skill",
    description=(
        "Save a reusable skill or approach to memory. "
        "If a skill with this name already exists it will be updated. "
        "Call this after successfully completing a multi-step task."
    ),
)
def remember_skill(
    name: str,
    content: str,
    description: str = "",
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    body = f"{description}\n\n{content}".strip() if description else content
    existing_list = _memory_store.search_by_tag("_skill", limit=200)
    existing = next((r for r in existing_list if r["title"].lower() == name.lower()), None)
    if existing:
        _memory_store.update_note(existing["note_id"], body, ["_skill"])
        return ToolResult.ok(f"Skill '{name}' updated.")
    note_id = _memory_store.remember(body, title=name, tags=["_skill"])
    return ToolResult.ok(f"Skill '{name}' saved (id={note_id[:8]}).")


@tool(
    name="recall_skill",
    description=(
        "Search your saved skills for one relevant to the current task. "
        "Call this before starting any complex or multi-step task."
    ),
)
def recall_skill(
    query: str,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    all_skills = _memory_store.search_by_tag("_skill", limit=200)
    if not all_skills:
        return ToolResult.ok("No skills saved yet.")
    q = query.lower()
    scored = []
    for s in all_skills:
        score = 0
        if q in (s.get("title") or "").lower():
            score += 2
        if q in (s.get("body") or "").lower():
            score += 1
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    matches = [s for _, s in scored[:3]] if scored else all_skills[:3]
    lines = []
    for s in matches:
        body_preview = (s.get("body") or "")[:300]
        lines.append(f"## {s['title']}\n{body_preview}")
    return ToolResult.ok("\n\n---\n\n".join(lines))


@tool(
    name="list_my_skills",
    description="List all skills you have learned and saved.",
)
def list_my_skills(
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    skills = _memory_store.search_by_tag("_skill", limit=200)
    if not skills:
        return ToolResult.ok("No skills saved yet.")
    lines = [f"- {s['title']}: {(s.get('body') or '').splitlines()[0][:80]}" for s in skills]
    return ToolResult.ok(f"{len(skills)} skills:\n" + "\n".join(lines))
