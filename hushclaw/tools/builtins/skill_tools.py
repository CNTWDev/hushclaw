"""Skill tools: list_skills and use_skill for OpenClaw-compatible SKILL.md files."""
from __future__ import annotations

from typing import TYPE_CHECKING

from hushclaw.tools.base import tool, ToolResult

if TYPE_CHECKING:
    from hushclaw.memory.store import MemoryStore
    from hushclaw.skills.loader import SkillRegistry


@tool(name="list_skills", description="List all available skills (installed SKILL.md packages).")
def list_skills(
    _skill_registry: "SkillRegistry | None" = None,
) -> ToolResult:
    if _skill_registry is None:
        return ToolResult.ok("No skill directory configured.")
    skills = _skill_registry.list_all()
    if not skills:
        return ToolResult.ok("No skills installed yet. Use remember_skill to create one.")
    available_count = sum(1 for s in skills if s.get("available", True))
    unavailable_count = len(skills) - available_count
    header = f"{len(skills)} skills ({available_count} available"
    if unavailable_count:
        header += f", {unavailable_count} unavailable"
    header += "):"
    lines = [header]
    _TIER_LABEL = {
        "builtin":   "builtin",
        "system":    "system",
        "user":      "user",
        "workspace": "workspace",
    }
    for s in skills:
        available = s.get("available", True)
        status = " [UNAVAILABLE]" if not available else ""
        reason = f" — {s['reason']}" if s.get("reason") else ""
        tier = _TIER_LABEL.get(s.get("tier", "user"), "user")
        lines.append(f"- {s['name']} [{tier}]{status}: {s['description']}{reason}")
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
    skill = _skill_registry.get(name)
    if skill is None:
        return ToolResult.error(
            f"Skill '{name}' not found. Use list_skills to see available skills."
        )
    if not skill.get("available", True):
        return ToolResult.error(
            f"Skill '{name}' is unavailable: {skill.get('reason', 'requirements not met')}. "
            "Install the required binaries or set the required environment variables first."
        )
    return ToolResult.ok(f"# Skill: {skill['name']}\n\n{skill['content']}")
