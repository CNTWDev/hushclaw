"""SkillRegistry: discover and load OpenClaw-compatible SKILL.md files."""
from __future__ import annotations

from pathlib import Path


class SkillRegistry:
    """Loads and indexes SKILL.md files from a directory tree."""

    def __init__(self, skill_dir: Path) -> None:
        self._skills: dict[str, dict] = {}  # name → {name, description, content, path}
        self._load(skill_dir)

    def _load(self, skill_dir: Path) -> None:
        for md_file in skill_dir.rglob("SKILL.md"):
            skill = self._parse(md_file)
            if skill["name"]:
                self._skills[skill["name"]] = skill

    def _parse(self, path: Path) -> dict:
        text = path.read_text(encoding="utf-8", errors="ignore")
        name, description, body = "", "", text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].splitlines():
                    if line.startswith("name:"):
                        name = line[5:].strip()
                    elif line.startswith("description:"):
                        description = line[12:].strip()
                body = parts[2].strip()
        if not name:
            name = path.parent.name
        return {"name": name, "description": description, "content": body, "path": str(path)}

    def get(self, name: str) -> dict | None:
        return self._skills.get(name) or next(
            (s for s in self._skills.values() if s["name"].lower() == name.lower()), None
        )

    def list_all(self) -> list[dict]:
        return [{"name": s["name"], "description": s["description"]} for s in self._skills.values()]

    def __len__(self) -> int:
        return len(self._skills)
