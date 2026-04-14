"""Memory tools: remember, recall, search_notes."""
from __future__ import annotations

from typing import TYPE_CHECKING

from hushclaw.tools.base import tool, ToolResult

if TYPE_CHECKING:
    from hushclaw.memory.store import MemoryStore
    from hushclaw.config.schema import Config


@tool(
    name="remember",
    description=(
        "Save important information to persistent memory for future sessions. "
        "content (required): the full text to remember. "
        "title: short headline (optional, auto-generated if omitted). "
        "Use scope='global' for user-level facts (name, preferences) shared across all agents. "
        "Leave scope empty to save to this agent's private namespace (default)."
    ),
)
def remember(
    content: str,
    title: str = "",
    tags: list = None,
    scope: str = "",
    _memory_store: "MemoryStore | None" = None,
    _config: "Config | None" = None,
) -> ToolResult:
    """Save a note to persistent memory."""
    if not content or not content.strip():
        return ToolResult.error("content cannot be empty — provide the text you want to remember")
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    # Determine effective scope: explicit > agent-scoped > global
    if not scope:
        ms = _config.agent.memory_scope if _config else ""
        scope = f"agent:{ms}" if ms else "global"
    note_id = _memory_store.remember(content, title=title, tags=tags or [], scope=scope)
    return ToolResult.ok(f"Saved to memory (id={note_id[:8]}, scope={scope})")


@tool(
    name="recall",
    description=(
        "Search and retrieve relevant memories from past sessions. "
        "Use this to recall user preferences, past decisions, project context, "
        "or anything previously saved with remember."
    ),
)
def recall(
    query: str = "",
    limit: int = 5,
    queries: str | list[str] | None = None,
    keywords: str | list[str] | None = None,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    """Search and return relevant memories."""
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    # Compatibility shim: some models call recall with `queries`/`keywords` instead of `query`.
    if not query and queries:
        if isinstance(queries, list):
            query = " ".join(str(q).strip() for q in queries if str(q).strip())
        else:
            query = str(queries).strip()
    if not query and keywords:
        if isinstance(keywords, list):
            query = " ".join(str(k).strip() for k in keywords if str(k).strip())
        else:
            query = str(keywords).strip()
    if not query.strip():
        return ToolResult.error("query is required")
    text = _memory_store.recall(query, limit=limit)
    return ToolResult.ok(text)


@tool(
    name="search_notes",
    description=(
        "Search notes by keyword or phrase and return matching titles and snippets. "
        "query (required): keyword or phrase to search for."
    ),
)
def search_notes(
    query: str,
    limit: int = 5,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    """Search notes and return structured results."""
    if not query or not query.strip():
        return ToolResult.error("query cannot be empty — provide a keyword or phrase to search for")
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
        "Save a reusable skill or approach as a SKILL.md file so it persists across sessions "
        "and is immediately available via use_skill. "
        "content must be structured workflow instructions (steps, rules, decision criteria) — "
        "never a copy of a memory note or conversation summary. "
        "name (required): unique skill identifier (short, kebab-case). "
        "content (required): the full skill instructions or steps. "
        "description: one-line summary shown in the Skills panel (optional). "
        "If a skill file with this name already exists it will be overwritten."
    ),
)
def remember_skill(
    name: str,
    content: str,
    description: str = "",
    _skill_manager=None,
) -> ToolResult:
    if not name or not name.strip():
        return ToolResult.error("name cannot be empty — provide a unique skill identifier")
    if not content or not content.strip():
        return ToolResult.error("content cannot be empty — provide the skill instructions to save")

    if _skill_manager is None:
        return ToolResult.error(
            "Skill manager not available. "
            "Set tools.user_skill_dir in hushclaw.toml first."
        )

    try:
        path = _skill_manager.create(name, content, description)
        return ToolResult.ok(f"Skill '{name}' saved to {path}. Available immediately via use_skill.")
    except ValueError as exc:
        return ToolResult.error(str(exc))
