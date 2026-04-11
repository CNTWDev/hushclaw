"""Write a user skill to a SKILL.md file on disk."""
from __future__ import annotations

import re
from pathlib import Path


def _slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def write_skill(
    name: str,
    content: str,
    description: str = "",
    skill_dir: "Path | str | None" = None,
) -> Path:
    """Write a SKILL.md file to *skill_dir*/{slug}/SKILL.md and return its path.

    The file is created (or overwritten) immediately — no registry reload is
    performed here; callers should call ``registry.reload()`` afterwards so the
    new skill is available without a server restart.
    """
    if skill_dir is None:
        raise ValueError("skill_dir must be provided")
    skill_dir = Path(skill_dir)
    slug = _slugify(name)
    skill_path = skill_dir / slug / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    fm_description = description.replace("\n", " ") if description else name
    skill_path.write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: {fm_description}\n"
        f"author: user\n"
        f'version: "1.0.0"\n'
        f"---\n\n"
        f"{content.strip()}\n",
        encoding="utf-8",
    )
    return skill_path
