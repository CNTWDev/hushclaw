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
    description="Search persistent memory and retrieve relevant information.",
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
        "Save a reusable skill or approach to memory. "
        "name (required): unique skill identifier (short, kebab-case). "
        "content (required): the full skill instructions or steps to save. "
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
    if not name or not name.strip():
        return ToolResult.error("name cannot be empty — provide a unique skill identifier")
    if not content or not content.strip():
        return ToolResult.error("content cannot be empty — provide the skill instructions to save")
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
        "Search ALL available skills (both installed skill packages and your saved skills) "
        "for one relevant to the current task. "
        "Call this before starting any complex or multi-step task — "
        "especially for document creation (PPT, Word, PDF), coding patterns, or domain workflows. "
        "If a matching skill is found its full instructions are returned; follow them directly."
    ),
)
def recall_skill(
    query: str = "",
    skill_name: str = "",
    _memory_store: "MemoryStore | None" = None,
    _skill_registry=None,
) -> ToolResult:
    # Compatibility: some models call recall_skill with skill_name instead of query.
    if not query and skill_name:
        query = skill_name
    if not query.strip():
        return ToolResult.error("query is required")

    lines: list[str] = []

    # ── 1. SkillRegistry (SKILL.md packages) — curated, highest priority ──────
    if _skill_registry is not None:
        q_low = query.lower()
        registry_matches = []
        for s in _skill_registry.list_all():
            if not s.get("available", True):
                continue
            score = 0
            name_low = s.get("name", "").lower()
            desc_low = s.get("description", "").lower()
            tags_low = " ".join(s.get("tags", [])).lower()
            # Forward: query token appears in skill name/desc/tags
            for word in q_low.split():
                if word in name_low:
                    score += 3
                if word in desc_low:
                    score += 2
                if word in tags_low:
                    score += 1
            # Reverse: skill name/tag token appears in full query (handles Chinese w/o spaces)
            for token in name_low.replace("-", " ").replace("_", " ").split():
                if len(token) >= 2 and token in q_low:
                    score += 3
            for tag in s.get("tags", []):
                tag_l = tag.lower()
                if len(tag_l) >= 2 and tag_l in q_low:
                    score += 1
            if score > 0:
                registry_matches.append((score, s))
        registry_matches.sort(key=lambda x: -x[0])
        for _, s in registry_matches[:2]:
            full = _skill_registry.get(s["name"])
            if full and full.get("content"):
                lines.append(
                    f"## Skill package: {full['name']}\n"
                    f"{full['description']}\n\n"
                    f"{full['content']}"
                )

    # ── 2. Memory skills (saved via remember_skill) ───────────────────────────
    if _memory_store is not None:
        all_mem = _memory_store.search_by_tag("_skill", limit=200)
        if all_mem:
            q_low = query.lower()
            scored = []
            for s in all_mem:
                score = 0
                if q_low in (s.get("title") or "").lower():
                    score += 2
                if q_low in (s.get("body") or "").lower():
                    score += 1
                if score > 0:
                    scored.append((score, s))
            scored.sort(key=lambda x: -x[0])
            matches = [s for _, s in scored[:2]] if scored else []
            for s in matches:
                if s.get("note_id"):
                    _memory_store.increment_recall_count(s["note_id"])
                body_preview = (s.get("body") or "")[:300]
                lines.append(f"## Saved skill: {s['title']}\n{body_preview}")

    if not lines:
        return ToolResult.ok(
            f"No skills found matching '{query}'. "
            "Proceed with your best judgment, and use remember_skill to save the approach afterward."
        )
    return ToolResult.ok("\n\n---\n\n".join(lines))


def _slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


@tool(
    name="promote_skill",
    description=(
        "Promote a saved memory skill to a SKILL.md file in skill_dir/auto-created/. "
        "name (required): exact skill name as saved via remember_skill. "
        "Only proceeds if the skill has been recalled at least auto_skill_promote_threshold "
        "times and the auto-created cap has not been reached."
    ),
)
def promote_skill(
    name: str,
    _memory_store: "MemoryStore | None" = None,
    _config: "Config | None" = None,
    _skill_registry: "SkillRegistry | None" = None,
) -> ToolResult:
    if not name or not name.strip():
        return ToolResult.error("name cannot be empty — provide the exact skill name as saved via remember_skill")
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
