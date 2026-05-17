"""Prompt blocks for skill progressive disclosure."""
from __future__ import annotations

from typing import Any

from hushclaw.prompt_blocks import PromptBlock, PromptRenderContext


def _clip(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _render_skill_index(skill_registry: Any, *, limit: int = 60) -> str:
    if skill_registry is None:
        return ""
    list_all = getattr(skill_registry, "list_all", None)
    if list_all is None:
        return ""
    try:
        skills = list(list_all() or [])
    except Exception:
        return ""
    visible = [
        skill
        for skill in skills
        if skill.get("enabled", True) and skill.get("available", True)
    ]
    if not visible:
        return ""
    visible.sort(key=lambda item: (str(item.get("tier") or item.get("scope") or ""), str(item.get("name") or "")))

    lines = [
        "## Available Skills",
        "Skills are reusable procedural instructions. This section is only an index, not the full skill body.",
        "When the task matches a skill, call `use_skill(name)` before applying it. Use `list_skills` for broader discovery.",
    ]
    for skill in visible[:limit]:
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        tier = str(skill.get("tier") or skill.get("scope") or "user")
        description = _clip(str(skill.get("description") or ""), 180)
        tags = ", ".join(str(tag) for tag in skill.get("tags") or [] if tag)
        suffix = f": {description}" if description else ""
        if tags:
            suffix += f" [tags: {tags}]"
        lines.append(f"- `{name}` [{tier}]{suffix}")
    remaining = len(visible) - limit
    if remaining > 0:
        lines.append(f"- ... {remaining} more skills. Call `list_skills` to inspect the full index.")
    return "\n".join(lines)


def build_skill_index_prompt_block(skill_registry: Any, *, limit: int = 60) -> PromptBlock:
    """Return a stable prompt block containing a compact skill index."""

    def _content(_context: PromptRenderContext) -> str:
        return _render_skill_index(skill_registry, limit=limit)

    return PromptBlock(
        id="kernel.skills.index",
        owner="kernel",
        tier="stable",
        priority=80,
        cacheable=True,
        title="Skill Index",
        content=_content,
    )
