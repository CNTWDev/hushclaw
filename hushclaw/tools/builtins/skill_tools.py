"""Skill tools: list_skills and use_skill for OpenClaw-compatible SKILL.md files."""
from __future__ import annotations

from typing import TYPE_CHECKING

from hushclaw.tools.base import tool, ToolResult

if TYPE_CHECKING:
    from hushclaw.skills.loader import SkillRegistry


@tool(name="list_skills", description="List all available OpenClaw-compatible skills.")
def list_skills(_skill_registry: "SkillRegistry | None" = None) -> ToolResult:
    if _skill_registry is None:
        return ToolResult.error("No skill_dir configured.")
    skills = _skill_registry.list_all()
    if not skills:
        return ToolResult.ok("No skills found.")

    lines = []
    for s in skills:
        available = s.get("available", True)
        status = " [UNAVAILABLE]" if not available else ""
        reason = f" — {s['reason']}" if s.get("reason") else ""
        lines.append(f"- {s['name']}{status}: {s['description']}{reason}")

    available_count = sum(1 for s in skills if s.get("available", True))
    unavailable_count = len(skills) - available_count
    header = f"{len(skills)} skills ({available_count} available"
    if unavailable_count:
        header += f", {unavailable_count} unavailable"
    header += "):"
    return ToolResult.ok(header + "\n" + "\n".join(lines))


@tool(
    name="use_skill",
    description=(
        "Load and return the instructions for a named skill. "
        "Read the returned instructions and follow them to complete the task."
    ),
)
def use_skill(name: str, _skill_registry: "SkillRegistry | None" = None) -> ToolResult:
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
