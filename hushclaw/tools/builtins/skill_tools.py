"""Skill tools: list_skills and use_skill for OpenClaw-compatible SKILL.md files."""
from __future__ import annotations

from typing import TYPE_CHECKING

from hushclaw.context.scanner import InjectedContentPolicy, scan_injected_text
from hushclaw.skills.contracts import SKILL_OUTPUT_CONTRACT
from hushclaw.tools.base import tool, ToolResult

if TYPE_CHECKING:
    from hushclaw.memory.store import MemoryStore
    from hushclaw.skills.loader import SkillRegistry


def _normalize_skill_name(name: str) -> str:
    """Normalize model/user-provided skill names for registry lookup."""
    return (name or "").strip().lstrip("/").strip()


@tool(name="list_skills", description="List all available skills (installed SKILL.md packages).")
def list_skills(
    query: str = "",
    limit: int = 40,
    _skill_registry: "SkillRegistry | None" = None,
) -> ToolResult:
    if _skill_registry is None:
        return ToolResult.ok("No skill directory configured.")
    if query.strip() and hasattr(_skill_registry, "search"):
        return search_skills(query=query, limit=limit, _skill_registry=_skill_registry)
    skills = _skill_registry.list_all()
    if not skills:
        return ToolResult.ok("No skills installed yet. Use remember_skill to create one.")
    limit = max(1, min(100, int(limit or 40)))
    total = len(skills)
    available_count = sum(1 for s in skills if s.get("available", True) and s.get("enabled", True))
    unavailable_count = len(skills) - available_count
    header = f"{total} skills ({available_count} available"
    if unavailable_count:
        header += f", {unavailable_count} unavailable"
    header += f"). Showing {min(limit, total)}. Use search_skills(query) for task-specific discovery:"
    lines = [header]
    _TIER_LABEL = {
        "builtin":   "builtin",
        "system":    "system",
        "user":      "user",
        "workspace": "workspace",
    }
    skills = sorted(skills, key=lambda item: (
        str(item.get("tier") or item.get("scope") or ""),
        str(item.get("name") or "").lower(),
    ))
    for s in skills[:limit]:
        available = s.get("available", True)
        status = " [UNAVAILABLE]" if not available else ""
        reason = f" — {s['reason']}" if s.get("reason") else ""
        tier = _TIER_LABEL.get(s.get("tier", "user"), "user")
        lines.append(f"- {s['name']} [{tier}]{status}: {s['description']}{reason}")
    remaining = total - limit
    if remaining > 0:
        lines.append(f"- ... {remaining} more skills omitted. Call search_skills(query) or list_skills(limit=N).")
    return ToolResult.ok("\n".join(lines))


@tool(
    name="search_skills",
    description=(
        "Search installed skills by task, topic, tool name, tag, or description. "
        "Use this before use_skill when the best skill is not already obvious."
    ),
)
def search_skills(
    query: str,
    limit: int = 10,
    _skill_registry: "SkillRegistry | None" = None,
) -> ToolResult:
    if _skill_registry is None:
        return ToolResult.ok("No skill directory configured.")
    query = " ".join(str(query or "").split())
    if not query:
        return ToolResult.error("query is required. Describe the task or skill you are looking for.")
    search = getattr(_skill_registry, "search", None)
    if search is None:
        return ToolResult.error("Skill registry does not support search.")
    result = search(query, limit=limit)
    items = result.get("items", [])
    if not items:
        return ToolResult.ok(f"No matching skills found for: {query}")
    lines = [
        f"{result.get('total', len(items))} matching skills for: {query}. "
        "Call use_skill(name) for the best match before applying it."
    ]
    for item in items:
        name = str(item.get("name") or "")
        scope = str(item.get("scope") or "user")
        description = str(item.get("description") or "")
        tags = ", ".join(str(tag) for tag in item.get("tags") or [] if tag)
        direct_tool = str(item.get("direct_tool") or "")
        suffix = f": {description}" if description else ""
        if tags:
            suffix += f" [tags: {tags}]"
        if direct_tool:
            suffix += f" [tool: {direct_tool}]"
        lines.append(f"- {name} [{scope}] score={item.get('score', 0)}{suffix}")
    return ToolResult.ok("\n".join(lines))


@tool(
    name="use_skill",
    description=(
        "Load and return the instructions for a named skill. "
        "Read the returned instructions and follow them to complete the task."
    ),
)
def use_skill(
    name: str,
    _skill_registry: "SkillRegistry | None" = None,
    _memory_store: "MemoryStore | None" = None,
    _session_id: str = "",
) -> ToolResult:
    if _skill_registry is None:
        return ToolResult.error("No skill_dir configured.")
    skill_name = _normalize_skill_name(name)
    if not skill_name:
        return ToolResult.error("Skill name cannot be empty. Use list_skills to see available skills.")
    skill = _skill_registry.get(skill_name)
    if skill is None:
        return ToolResult.error(
            f"Skill '{skill_name}' not found. Use list_skills to see available skills."
        )
    if not skill.get("available", True):
        return ToolResult.error(
            f"Skill '{name}' is unavailable: {skill.get('reason', 'requirements not met')}. "
            "Install the required binaries or set the required environment variables first."
        )
    scanned = scan_injected_text(
        str(skill.get("content") or ""),
        source=f"skill:{skill['name']}",
        kind="skill_content",
        trusted=False,
        wrap=False,
        policy=InjectedContentPolicy(),
    )
    if scanned.dropped:
        return ToolResult.error(
            f"Skill '{skill['name']}' was withheld because it matched high-risk prompt injection patterns."
        )
    note = ""
    labels = scanned.metadata.get("threat_labels") or []
    if labels:
        note = (
            "## Skill Security Note\n"
            f"Threat labels detected: {', '.join(str(label) for label in labels)}.\n\n"
        )
    return ToolResult.ok(
        f"# Skill: {skill['name']}\n\n{SKILL_OUTPUT_CONTRACT}\n\n{note}{scanned.text}"
    )


@tool(
    name="skill_view",
    description=(
        "Alias for use_skill. Load and return the full instructions for a named skill "
        "after seeing it in the skill index."
    ),
)
def skill_view(
    name: str,
    _skill_registry: "SkillRegistry | None" = None,
    _memory_store: "MemoryStore | None" = None,
    _session_id: str = "",
) -> ToolResult:
    return use_skill(
        name,
        _skill_registry=_skill_registry,
        _memory_store=_memory_store,
        _session_id=_session_id,
    )
