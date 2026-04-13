"""install_skill — built-in tool for installing HushClaw skills from any source.

Supports: local directory, local ZIP, HTTPS ZIP URL, Git repository URL.
Delegates all install logic to SkillManager (_skill_manager injection).
"""
from __future__ import annotations

import json

from hushclaw.tools.base import ToolResult, tool


@tool(
    name="install_skill",
    description=(
        "Install a HushClaw skill from a local path or URL. "
        "source: absolute or tilde-expanded path to a skill directory or .zip file, "
        "or a git/HTTPS URL pointing to a skill repository or zip download. "
        "skill_name: optional slug override (used as the install directory name). "
        "Validates SKILL.md, checks compatibility, installs pip dependencies, "
        "reloads the skill registry, loads bundled tool plugins, and records the "
        "install in .skill-lock.json. Returns a JSON installation report."
    ),
)
async def install_skill(
    source: str,
    skill_name: str = "",
    _skill_manager=None,
) -> ToolResult:
    if _skill_manager is None:
        return ToolResult.error(
            "Skill manager not available. "
            "This is an internal error — please report it."
        )
    result = await _skill_manager.install(source, slug=skill_name or None)
    if not result.ok:
        return ToolResult.error(result.error)
    return ToolResult.ok(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
