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
    lines = [f"- {s['name']}: {s['description']}" for s in skills]
    return ToolResult.ok(f"{len(skills)} skills available:\n" + "\n".join(lines))


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
    return ToolResult.ok(f"# Skill: {skill['name']}\n\n{skill['content']}")
