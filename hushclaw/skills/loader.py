"""SkillRegistry: discover and load OpenClaw-compatible SKILL.md files."""
from __future__ import annotations

import json
import os
import shutil
import sys
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
    Parse the legacy top-level ``requires:`` block from frontmatter text.

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


def _format_install_cmd(spec: dict) -> str:
    """Format an install spec dict into a human-readable shell command."""
    kind = spec.get("kind", "")
    if kind == "brew":
        formula = spec.get("formula", "")
        return f"brew install {formula}" if formula else ""
    if kind == "pip":
        package = spec.get("package", "")
        return f"pip install {package}" if package else ""
    if kind == "npm":
        package = spec.get("package", "")
        return f"npm install -g {package}" if package else ""
    if kind == "go":
        package = spec.get("package", "")
        return f"go install {package}" if package else ""
    if kind == "download":
        url = spec.get("url", "")
        label = spec.get("label", "")
        return label or url
    label = spec.get("label", "")
    return label


def _parse_metadata_json(
    frontmatter_text: str,
) -> tuple[list[str], list[str], list[str], list[dict]]:
    """
    Parse the OpenClaw new-style single-line ``metadata:`` JSON field.

    Supports::

        metadata: {"openclaw":{"requires":{"bins":["git"],"env":["TOKEN"]},"os":["darwin"]}}

    Returns ``(bins, env, os_list, install_specs)``.
    ``os_list`` uses OpenClaw platform names: ``darwin``, ``linux``, ``win32``.
    ``install_specs`` is the raw ``install`` array from the metadata.
    """
    bins: list[str] = []
    env: list[str] = []
    os_list: list[str] = []
    install_specs: list[dict] = []

    for line in frontmatter_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("metadata:"):
            continue
        raw_json = stripped[9:].strip()
        if not raw_json:
            continue
        try:
            data = json.loads(raw_json)
        except (json.JSONDecodeError, ValueError):
            continue
        # Support both "openclaw" and "clawdbot" platform keys (same structure)
        openclaw = (
            data.get("openclaw") or data.get("clawdbot") or {}
        ) if isinstance(data, dict) else {}
        requires = openclaw.get("requires", {}) if isinstance(openclaw, dict) else {}
        if isinstance(requires, dict):
            raw_bins = requires.get("bins", [])
            raw_env = requires.get("env", [])
            if isinstance(raw_bins, list):
                bins = [str(b) for b in raw_bins]
            if isinstance(raw_env, list):
                env = [str(e) for e in raw_env]
        raw_os = openclaw.get("os", []) if isinstance(openclaw, dict) else []
        if isinstance(raw_os, list):
            os_list = [str(o) for o in raw_os]
        raw_install = openclaw.get("install", []) if isinstance(openclaw, dict) else []
        if isinstance(raw_install, list):
            install_specs = [s for s in raw_install if isinstance(s, dict)]
        break  # only process first metadata line

    return bins, env, os_list, install_specs


def _check_os(os_list: list[str]) -> tuple[bool, str]:
    """
    Check if the current platform matches the skill's ``os`` requirement.

    ``os_list`` uses OpenClaw names: ``darwin``, ``linux``, ``win32``.
    Empty list = no restriction (always passes).

    Returns ``(available: bool, reason: str)``.
    """
    if not os_list:
        return True, ""
    platform = sys.platform  # e.g. "darwin", "linux", "win32"
    if platform in os_list:
        return True, ""
    return False, f"Requires OS: {', '.join(os_list)}"


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

    Front-matter compatibility
    --------------------------
    Supports both the legacy HushClaw / AgentSkills format and the OpenClaw
    new-style ``metadata:`` single-line JSON field:

    Legacy (still supported)::

        requires:
          bins: [git]
          env: [GITHUB_TOKEN]

    New-style OpenClaw::

        metadata: {"openclaw":{"requires":{"bins":["git"],"env":["TOKEN"]},"os":["darwin"]}}

    Both formats are merged; if a field appears in both, the new-style value
    is appended (deduplication is not performed — the union is checked).

    Additional recognised fields (stored for UI / provenance; not required)::

        author, version, license, homepage, source, include_files
    """

    def __init__(self, skill_dirs: "Path | list[Path]") -> None:
        if isinstance(skill_dirs, Path):
            skill_dirs = [skill_dirs]
        self._skill_dirs: list[Path] = list(skill_dirs)
        self._skills: dict[str, dict] = {}  # name → metadata dict
        self._do_load()

    def _do_load(self) -> None:
        """Clear and reload all skill directories (built-ins + configured dirs)."""
        self._skills = {}
        # Built-ins first (lowest priority)
        if _BUILTINS_DIR.exists():
            self._load(_BUILTINS_DIR, tier="builtin")
        # User / workspace dirs in ascending priority
        for d in self._skill_dirs:
            if d:
                self._load(d, tier="user")

    def reload(self) -> None:
        """Rescan all configured skill directories and refresh the registry.

        Call this after writing a new SKILL.md to disk so the skill is
        immediately available without a server restart.
        """
        self._do_load()

    # ------------------------------------------------------------------
    # Internal loading
    # ------------------------------------------------------------------

    _SKIP_DIRS = {"staging", "clawhub", ".git", "__pycache__", "node_modules"}

    def _load(self, skill_dir: Path, tier: str = "user") -> None:
        if not skill_dir or not skill_dir.exists():
            return
        for md_file in skill_dir.rglob("SKILL.md"):
            # Skip staging / scratch directories that should not be auto-loaded
            if any(part in self._SKIP_DIRS for part in md_file.parts):
                continue
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
        # Provenance / display fields
        author = ""
        version = ""
        license_ = ""
        homepage = ""
        source = ""
        include_files: list[str] = []
        install_specs: list[dict] = []

        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                fm = parts[1]

                for line in fm.splitlines():
                    ls = line.strip()
                    if ls.startswith("name:"):
                        name = ls[5:].strip().strip('"').strip("'")
                    elif ls.startswith("description:"):
                        description = ls[12:].strip().strip('"').strip("'")
                    elif ls.startswith("direct_tool:"):
                        direct_tool = ls[12:].strip().strip('"').strip("'")
                    elif ls.startswith("command-tool:"):
                        # OpenClaw alias for direct_tool
                        if not direct_tool:
                            direct_tool = ls[13:].strip().strip('"').strip("'")
                    elif ls.startswith("tags:"):
                        tags = _parse_yaml_list(ls[5:].strip())
                    elif ls.startswith("author:"):
                        author = ls[7:].strip().strip('"').strip("'")
                    elif ls.startswith("version:"):
                        version = ls[8:].strip().strip('"').strip("'")
                    elif ls.startswith("license:"):
                        license_ = ls[8:].strip().strip('"').strip("'")
                    elif ls.startswith("homepage:"):
                        homepage = ls[9:].strip().strip('"').strip("'")
                    elif ls.startswith("source:"):
                        source = ls[7:].strip().strip('"').strip("'")
                    elif ls.startswith("include_files:"):
                        include_files = _parse_yaml_list(ls[14:].strip())

                # Legacy top-level requires block
                legacy_bins, legacy_env = _parse_requires(fm)
                requires_bins.extend(legacy_bins)
                requires_env.extend(legacy_env)

                # New-style metadata JSON (OpenClaw / clawdbot)
                meta_bins, meta_env, os_list, install_specs = _parse_metadata_json(fm)
                requires_bins.extend(meta_bins)
                requires_env.extend(meta_env)

                # Deduplicate while preserving order
                requires_bins = list(dict.fromkeys(requires_bins))
                requires_env = list(dict.fromkeys(requires_env))
            else:
                os_list = []
        else:
            os_list = []

        if not name:
            name = path.parent.name

        # Availability: OS check first, then binary/env check
        os_ok, os_reason = _check_os(os_list)
        req_ok, req_reason = _check_requirements(requires_bins, requires_env)

        if not os_ok:
            available, reason = False, os_reason
        elif not req_ok:
            available, reason = False, req_reason
        else:
            available, reason = True, ""

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
            "os_list": os_list,
            "available": available,
            "reason": reason,
            # Provenance / display
            "author": author,
            "version": version,
            "license_": license_,
            "homepage": homepage,
            "source": source,
            "include_files": include_files,
            "install_specs": install_specs,
        }

    def _load_content(self, skill: dict) -> str:
        """Read the full SKILL.md body (lazy load).

        Post-processing applied:
        1. ``{baseDir}`` is replaced with the skill directory's absolute path,
           enabling ClawHub scripts like ``python {baseDir}/scripts/foo.py``.
        2. Files listed in ``include_files`` are appended as appendix sections,
           after a path-traversal safety check.
        """
        text = Path(skill["path"]).read_text(encoding="utf-8", errors="ignore")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                body = parts[2].strip()
            else:
                body = text
        else:
            body = text

        skill_dir = Path(skill["path"]).parent

        # 1. Expand {baseDir} placeholder
        body = body.replace("{baseDir}", str(skill_dir))

        # 2. Inline include_files
        for rel_path in skill.get("include_files", []):
            candidate = (skill_dir / rel_path).resolve()
            # Path-traversal guard
            try:
                candidate.relative_to(skill_dir.resolve())
            except ValueError:
                continue  # silently skip unsafe paths
            if not candidate.is_file():
                continue
            try:
                extra = candidate.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            body += f"\n\n---\n## Appendix: {rel_path}\n\n{extra.strip()}"

        return body

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
                # Provenance fields
                "author":      s.get("author", ""),
                "version":     s.get("version", ""),
                "license":     s.get("license_", ""),
                "homepage":    s.get("homepage", ""),
                "source":      s.get("source", ""),
                "install_hints": [
                    {"kind": spec.get("kind", ""), "cmd": _format_install_cmd(spec)}
                    for spec in s.get("install_specs", [])
                    if _format_install_cmd(spec)
                ],
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
            "os_list": [],
            "available": available,
            "reason": reason,
            "author": "",
            "version": "",
            "license_": "",
            "homepage": "",
            "source": "",
            "include_files": [],
            "install_specs": [],
        }

    def __len__(self) -> int:
        return len(self._skills)
