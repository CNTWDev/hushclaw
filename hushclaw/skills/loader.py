"""SkillRegistry: discover and load OpenClaw-compatible SKILL.md files."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# Built-in skills bundled with the package
_BUILTINS_DIR = Path(__file__).parent / "builtins"


# ---------------------------------------------------------------------------
# Front-matter parsing helpers
# ---------------------------------------------------------------------------

def _parse_yaml_list(raw: str) -> list[str]:
    """Parse a simple YAML inline list: '["a", "b"]' or '[a, b]'."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        items = [s.strip().strip('"').strip("'") for s in inner.split(",")]
        return [i for i in items if i]
    return []


def _parse_requires(frontmatter_text: str) -> tuple[list[str], list[str]]:
    """
    Parse the `requires:` block from frontmatter text.

    Supports::

        requires:
          bins: [git, gh]
          env: [GITHUB_TOKEN]

    Returns ``(bins, env)`` as lists of strings.
    """
    bins: list[str] = []
    env: list[str] = []
    in_requires = False
    for line in frontmatter_text.splitlines():
        stripped = line.strip()
        if stripped == "requires:":
            in_requires = True
            continue
        if in_requires:
            if stripped.startswith("bins:"):
                bins = _parse_yaml_list(stripped[5:].strip())
            elif stripped.startswith("env:"):
                env = _parse_yaml_list(stripped[4:].strip())
            elif stripped and not stripped.startswith("#") and ":" in stripped:
                # New top-level key — exit requires block if no leading indent
                if not line.startswith(" ") and not line.startswith("\t"):
                    in_requires = False
    return bins, env


def _check_requirements(
    bins: list[str], env: list[str]
) -> tuple[bool, str]:
    """
    Check if all requirement bins and env vars are satisfied.

    Returns ``(available: bool, reason: str)``.
    *reason* is empty string when *available* is True.
    """
    missing_bins = [b for b in bins if shutil.which(b) is None]
    missing_env = [e for e in env if not os.environ.get(e)]
    if not missing_bins and not missing_env:
        return True, ""
    parts = []
    if missing_bins:
        parts.append(f"Missing binaries: {', '.join(missing_bins)}")
    if missing_env:
        parts.append(f"Missing env vars: {', '.join(missing_env)}")
    return False, "; ".join(parts)


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------

class SkillRegistry:
    """Loads and indexes SKILL.md files from a directory tree.

    Priority order (ascending — later overrides earlier):

    1. Built-in skills (``hushclaw/skills/builtins/``) — always lowest
    2. Directories listed in *skill_dirs* (left-to-right, later wins)

    In practice the caller passes::

        [system_skill_dir, user_skill_dir, workspace_skill_dir]

    so workspace skills have the highest priority.

    Backward-compatible: a single ``Path`` is accepted in place of a list.
    """

    def __init__(self, skill_dirs: "Path | list[Path]") -> None:
        if isinstance(skill_dirs, Path):
            skill_dirs = [skill_dirs]
        self._skills: dict[str, dict] = {}  # name → metadata dict
        # Built-ins first (lowest priority)
        if _BUILTINS_DIR.exists():
            self._load(_BUILTINS_DIR, tier="builtin")
        # User / workspace dirs in ascending priority
        for d in skill_dirs:
            if d:
                self._load(d, tier="user")

    # ------------------------------------------------------------------
    # Internal loading
    # ------------------------------------------------------------------

    def _load(self, skill_dir: Path, tier: str = "user") -> None:
        if not skill_dir or not skill_dir.exists():
            return
        for md_file in skill_dir.rglob("SKILL.md"):
            skill = self._parse(md_file, tier=tier)
            if skill["name"]:
                self._skills[skill["name"]] = skill

    def _parse(self, path: Path, tier: str = "user") -> dict:
        """Read front-matter and compute availability; body is loaded lazily."""
        text = path.read_text(encoding="utf-8", errors="ignore")
        name = ""
        description = ""
        direct_tool = ""
        tags: list[str] = []
        requires_bins: list[str] = []
        requires_env: list[str] = []

        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                fm = parts[1]
                for line in fm.splitlines():
                    line_stripped = line.strip()
                    if line_stripped.startswith("name:"):
                        name = line_stripped[5:].strip().strip('"').strip("'")
                    elif line_stripped.startswith("description:"):
                        description = line_stripped[12:].strip().strip('"').strip("'")
                    elif line_stripped.startswith("direct_tool:"):
                        direct_tool = line_stripped[12:].strip().strip('"').strip("'")
                    elif line_stripped.startswith("tags:"):
                        tags = _parse_yaml_list(line_stripped[5:].strip())
                requires_bins, requires_env = _parse_requires(fm)

        if not name:
            name = path.parent.name

        available, reason = _check_requirements(requires_bins, requires_env)

        return {
            "name": name,
            "description": description,
            "content": None,          # lazy-loaded on first get()
            "path": str(path),
            "tier": tier,             # "builtin" | "user"
            "tags": tags,
            "direct_tool": direct_tool,
            "requires_bins": requires_bins,
            "requires_env": requires_env,
            "available": available,
            "reason": reason,
        }

    def _load_content(self, skill: dict) -> str:
        """Read the full SKILL.md body (lazy load)."""
        text = Path(skill["path"]).read_text(encoding="utf-8", errors="ignore")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()
        return text

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, name: str) -> dict | None:
        skill = self._skills.get(name) or next(
            (s for s in self._skills.values() if s["name"].lower() == name.lower()),
            None,
        )
        if skill is None:
            return None
        if skill["content"] is None:
            skill["content"] = self._load_content(skill)
        return skill

    def list_all(self) -> list[dict]:
        return [
            {
                "name":        s["name"],
                "description": s["description"],
                "builtin":     s.get("tier") == "builtin",
                "tags":        s.get("tags", []),
                "available":   s.get("available", True),
                "reason":      s.get("reason", ""),
                "direct_tool": s.get("direct_tool", ""),
            }
            for s in self._skills.values()
        ]

    def register_skill(
        self,
        name: str,
        description: str,
        path: str,
        available: bool = True,
        reason: str = "",
    ) -> None:
        """Register a skill entry directly (e.g. after auto-creating a SKILL.md)."""
        self._skills[name] = {
            "name": name,
            "description": description,
            "content": None,
            "path": path,
            "tier": "user",
            "tags": [],
            "direct_tool": "",
            "requires_bins": [],
            "requires_env": [],
            "available": available,
            "reason": reason,
        }

    def __len__(self) -> int:
        return len(self._skills)
