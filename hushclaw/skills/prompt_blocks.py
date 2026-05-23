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
    visible.sort(key=lambda item: (
        str(item.get("tier") or item.get("scope") or ""),
        str(item.get("name") or "").lower(),
    ))

    pinned = [
        skill
        for skill in visible
        if str(skill.get("tier") or skill.get("scope") or "") in {"workspace", "user"}
    ]
    if not pinned:
        pinned = visible
    display_limit = min(max(0, int(limit or 0)), 20)

    lines = [
        "## Skill Discovery",
        f"{len(visible)} enabled skills are available. This section is a compact discovery protocol, not the full skill index.",
        "If the best skill is obvious from the current task, call `use_skill(name)` before applying it.",
        "If the best skill is not obvious, call `search_skills(query)` with a task-focused query, then call `use_skill(name)` for the best match.",
        "Use `list_skills` only for broad browsing or when search is insufficient.",
    ]
    if display_limit > 0:
        lines.append("High-priority skill hints:")
        for skill in pinned[:display_limit]:
            name = str(skill.get("name") or "").strip()
            if not name:
                continue
            tier = str(skill.get("tier") or skill.get("scope") or "user")
            description = _clip(str(skill.get("description") or ""), 120)
            tags = ", ".join(str(tag) for tag in skill.get("tags") or [] if tag)
            suffix = f": {description}" if description else ""
            if tags:
                suffix += f" [tags: {tags}]"
            lines.append(f"- `{name}` [{tier}]{suffix}")
    remaining = len(visible) - display_limit
    if remaining > 0:
        lines.append(f"- ... {remaining} more skills are searchable with `search_skills(query)`.")
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
