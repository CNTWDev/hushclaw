"""Tools for evolving reusable skills from repeated workflows."""
from __future__ import annotations

from hushclaw.tools.base import tool, ToolResult


@tool(
    name="evolve_skill",
    description=(
        "Create or update a reusable skill from a repeated workflow or reflection. "
        "mode: create | patch | rewrite. "
        "skill_name: target skill name. "
        "observation: what changed or what was learned. "
        "workflow: optional reusable instructions when creating or rewriting."
    ),
)
def evolve_skill(
    skill_name: str,
    mode: str = "patch",
    observation: str = "",
    workflow: str = "",
    description: str = "",
    _skill_manager=None,
) -> ToolResult:
    if _skill_manager is None:
        return ToolResult.error("Skill manager not available.")
    if not skill_name or not skill_name.strip():
        return ToolResult.error("skill_name is required")
    mode = (mode or "patch").strip().lower()
    try:
        if mode == "create":
            content = workflow.strip() or f"## Workflow\n- {observation.strip() or 'Reusable workflow'}\n"
            path = _skill_manager.create(skill_name.strip(), content, description=description)
        elif mode == "rewrite":
            content = workflow.strip() or observation.strip()
            if not content:
                return ToolResult.error("workflow or observation is required for rewrite mode")
            path = _skill_manager.edit(skill_name.strip(), content, description=description)
        else:
            if not observation.strip():
                return ToolResult.error("observation is required for patch mode")
            path = _skill_manager.patch(skill_name.strip(), observation.strip())
        return ToolResult.ok(f"Skill '{skill_name}' evolved at {path}")
    except ValueError as exc:
        return ToolResult.error(str(exc))
