"""Skill tools: list_skills and use_skill for OpenClaw-compatible SKILL.md files."""
from __future__ import annotations

from typing import TYPE_CHECKING

from hushclaw.tools.base import tool, ToolResult

if TYPE_CHECKING:
    from hushclaw.skills.loader import SkillRegistry
    from hushclaw.memory.store import MemoryStore


@tool(name="list_skills", description="List all available skills (installed packages and learned skills).")
def list_skills(
    _skill_registry: "SkillRegistry | None" = None,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    lines = []

    # 1. Installed SKILL.md packages
    if _skill_registry is not None:
        skills = _skill_registry.list_all()
        if skills:
            available_count = sum(1 for s in skills if s.get("available", True))
            unavailable_count = len(skills) - available_count
            header = f"{len(skills)} installed skills ({available_count} available"
            if unavailable_count:
                header += f", {unavailable_count} unavailable"
            header += "):"
            lines.append(header)
            for s in skills:
                available = s.get("available", True)
                status = " [UNAVAILABLE]" if not available else ""
                reason = f" — {s['reason']}" if s.get("reason") else ""
                provenance = ""
                if s.get("author"):
                    provenance += f" (by {s['author']})"
                if s.get("license"):
                    provenance += f" [{s['license']}]"
                lines.append(f"- {s['name']}{status}: {s['description']}{provenance}{reason}")

    # 2. Learned skills saved via remember_skill
    if _memory_store is not None:
        mem_skills = _memory_store.search_by_tag("_skill", limit=200)
        if mem_skills:
            lines.append(f"\n{len(mem_skills)} learned skills (use recall_skill to load full instructions):")
            for s in mem_skills:
                first_line = (s.get("body") or "").splitlines()[0][:80]
                rc = s.get("recall_count") or 0
                lines.append(f"- {s['title']}: {first_line} (recalled {rc}x)")

    if not lines:
        return ToolResult.ok("No skills found.")
    return ToolResult.ok("\n".join(lines))


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
