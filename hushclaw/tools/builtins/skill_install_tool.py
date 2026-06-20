"""Skill source inspection and installation tools.

Supports: local directory, local ZIP, HTTPS ZIP URL, Git repository URL.
Delegates all install logic to SkillManager (_skill_manager injection).
"""
from __future__ import annotations

import json

from hushclaw.tools.base import ToolResult, tool


@tool(
    name="inspect_skill_source",
    description=(
        "Inspect an external HushClaw skill source before installation. "
        "Supports local paths, zip files, git URLs, GitHub tree URLs, and Claude-style plugin repos. "
        "Returns normalized source metadata, discovered skill candidates, warnings, and the default install scope."
    ),
)
async def inspect_skill_source(
    source: str,
    source_ref: str = "",
    source_subpath: str = "",
    _skill_manager=None,
) -> ToolResult:
    if _skill_manager is None:
        return ToolResult.error(
            "Skill manager not available. "
            "This is an internal error — please report it."
        )
    try:
        result = await _skill_manager.inspect_source(
            source,
            ref=source_ref or "",
            subpath=source_subpath or "",
        )
    except Exception as exc:
        return ToolResult.error(str(exc))
    return ToolResult.ok(json.dumps(result, indent=2, ensure_ascii=False))


@tool(
    name="install_skill",
    description=(
        "Install a HushClaw skill from a local path or URL. "
        "source: absolute or tilde-expanded path to a skill directory or .zip file, "
        "or a git/HTTPS URL pointing to a skill repository or zip download. "
        "skill_name: optional slug override (used as the install directory name). "
        "scope: install target, either 'user' (default) or 'workspace'. "
        "source_ref/source_subpath: optional normalized source selectors, useful after inspecting a multi-skill repo. "
        "Validates SKILL.md, checks compatibility, installs pip dependencies, "
        "reloads the skill registry, loads bundled tool plugins, and records the "
        "install in .skill-lock.json. Returns a JSON installation report."
    ),
)
async def install_skill(
    source: str,
    skill_name: str = "",
    scope: str = "user",
    source_ref: str = "",
    source_subpath: str = "",
    _skill_manager=None,
) -> ToolResult:
    if _skill_manager is None:
        return ToolResult.error(
            "Skill manager not available. "
            "This is an internal error — please report it."
        )
    tier = "workspace" if str(scope or "user").strip().lower() == "workspace" else "user"
    result = await _skill_manager.install(
        source,
        slug=skill_name or None,
        tier=tier,
        ref=source_ref or "",
        subpath=source_subpath or "",
    )
    if not result.ok:
        return ToolResult.error(result.error)
    return ToolResult.ok(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
