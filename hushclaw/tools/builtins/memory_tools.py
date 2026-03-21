"""Memory tools: remember, recall, search_notes."""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from hushclaw.tools.base import tool, ToolResult

if TYPE_CHECKING:
    from hushclaw.memory.store import MemoryStore
    from hushclaw.config.schema import Config
    from hushclaw.skills.loader import SkillRegistry


@tool(
    name="remember",
    description=(
        "Save important information to persistent memory for future sessions. "
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
    # Increment recall_count for each matched skill
    for s in matches:
        if s.get("note_id"):
            _memory_store.increment_recall_count(s["note_id"])
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


def _slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


@tool(
    name="promote_skill",
    description=(
        "Promote a saved memory skill to a SKILL.md file in skill_dir/auto-created/. "
        "Only proceeds if the skill has been recalled at least auto_skill_promote_threshold "
        "times and the auto-created cap has not been reached. "
        "Call with the exact skill name as saved via remember_skill."
    ),
)
def promote_skill(
    name: str,
    _memory_store: "MemoryStore | None" = None,
    _config: "Config | None" = None,
    _skill_registry: "SkillRegistry | None" = None,
) -> ToolResult:
    if _memory_store is None:
        return ToolResult.error("Memory store not available")
    if _config is None:
        return ToolResult.error("Config not available")
    # Prefer user_skill_dir so auto-promoted skills don't mix into the system dir
    skill_dir = _config.tools.user_skill_dir or _config.tools.skill_dir
    if skill_dir is None:
        return ToolResult.error(
            "No skill directory configured. Set tools.user_skill_dir or tools.skill_dir in hushclaw.toml."
        )

    # Look up the memory skill
    all_skills = _memory_store.search_by_tag("_skill", limit=200)
    entry = next((s for s in all_skills if s["title"].lower() == name.lower()), None)
    if entry is None:
        return ToolResult.error(f"No saved skill named '{name}'. Use remember_skill to save it first.")

    recall_count = entry.get("recall_count") or 0
    threshold = _config.tools.auto_skill_promote_threshold
    if recall_count < threshold:
        return ToolResult.ok(
            f"Skill '{name}' has been recalled {recall_count} time(s); "
            f"need at least {threshold} before promotion. Keep using it!"
        )

    # Check if a SKILL.md with this name already exists
    if _skill_registry is not None and _skill_registry.get(name) is not None:
        return ToolResult.ok(f"Skill '{name}' is already registered as a SKILL.md — no promotion needed.")

    # Count existing auto-created skills
    auto_dir = Path(skill_dir) / "auto-created"
    auto_dir.mkdir(parents=True, exist_ok=True)
    existing = [p for p in auto_dir.iterdir() if p.is_dir() and (p / "SKILL.md").exists()]
    cap = _config.tools.auto_skill_cap
    if len(existing) >= cap:
        return ToolResult.error(
            f"Auto-created skill cap reached ({cap}). "
            "Remove or archive some skills in skill_dir/auto-created/ first."
        )

    # Write SKILL.md
    slug = _slugify(name)
    skill_path = auto_dir / slug / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    body = (entry.get("body") or "").strip()
    content = f"---\nname: {name}\ndescription: Auto-promoted from memory (recalled {recall_count}x)\nauthor: hushclaw-auto\nversion: \"1.0.0\"\n---\n\n{body}\n"
    skill_path.write_text(content, encoding="utf-8")

    # Register in the live SkillRegistry so it's usable without restart
    if _skill_registry is not None:
        _skill_registry.register_skill(
            name=name,
            description=f"Auto-promoted from memory (recalled {recall_count}x)",
            path=str(skill_path),
        )

    return ToolResult.ok(
        f"Skill '{name}' promoted to {skill_path}. "
        "It is now available via use_skill and will persist across restarts."
    )
