"""SkillRegistry: discover and load OpenClaw-compatible SKILL.md files."""
from __future__ import annotations

from pathlib import Path

# Built-in skills bundled with the package
_BUILTINS_DIR = Path(__file__).parent / "builtins"


class SkillRegistry:
    """Loads and indexes SKILL.md files from a directory tree.

    Built-in skills (hushclaw/skills/builtins/) are always loaded first.
    User-installed skills in *skill_dir* are loaded second and can
    override built-ins with the same name.
    """

    def __init__(self, skill_dir: Path) -> None:
        self._skills: dict[str, dict] = {}  # name → {name, description, content, path}
        # Load built-ins first so user skills can override them
        if _BUILTINS_DIR.exists():
            self._load(_BUILTINS_DIR)
        self._load(skill_dir)

    def _load(self, skill_dir: Path) -> None:
        if not skill_dir.exists():
            return
        for md_file in skill_dir.rglob("SKILL.md"):
            skill = self._parse(md_file)
            if skill["name"]:
                self._skills[skill["name"]] = skill

    def _parse(self, path: Path) -> dict:
        """Read only front-matter (name, description) — body is loaded on demand."""
        text = path.read_text(encoding="utf-8", errors="ignore")
        name, description = "", ""
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].splitlines():
                    if line.startswith("name:"):
                        name = line[5:].strip()
                    elif line.startswith("description:"):
                        description = line[12:].strip()
        if not name:
            name = path.parent.name
        # content=None → loaded lazily on first get()
        return {"name": name, "description": description, "content": None, "path": str(path)}

    def _load_content(self, skill: dict) -> str:
        """Read the full SKILL.md body for a skill entry (lazy load)."""
        text = Path(skill["path"]).read_text(encoding="utf-8", errors="ignore")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return text

    def get(self, name: str) -> dict | None:
        skill = self._skills.get(name) or next(
            (s for s in self._skills.values() if s["name"].lower() == name.lower()), None
        )
        if skill is None:
            return None
        if skill["content"] is None:
            skill["content"] = self._load_content(skill)
        return skill

    def list_all(self) -> list[dict]:
        return [
            {
                "name": s["name"],
                "description": s["description"],
                "builtin": str(_BUILTINS_DIR) in s.get("path", ""),
            }
            for s in self._skills.values()
        ]

    def register_skill(self, name: str, description: str, path: str) -> None:
        """Register a skill entry directly (e.g. after auto-creating a SKILL.md)."""
        self._skills[name] = {"name": name, "description": description, "content": None, "path": path}

    def __len__(self) -> int:
        return len(self._skills)
