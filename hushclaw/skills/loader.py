"""SkillRegistry: discover and load OpenClaw-compatible SKILL.md files."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from time import time

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


def _search_tokens(value: str) -> list[str]:
    """Return normalized lexical search tokens for lightweight skill routing."""
    return [token for token in re.split(r"[^a-z0-9]+", value.lower()) if token]


def _contains_path(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _marketplace_plugin_skill_roots(skill_dir: Path) -> set[Path]:
    """Return plugin skill roots declared by a top-level Claude marketplace file."""
    manifest = skill_dir / ".claude-plugin" / "marketplace.json"
    if not manifest.exists():
        return set()
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return set()
    roots: set[Path] = set()
    for plugin in data.get("plugins") or []:
        if not isinstance(plugin, dict):
            continue
        source_rel = str(plugin.get("source") or "").strip()
        if not source_rel:
            continue
        plugin_root = (skill_dir / source_rel).resolve()
        plugin_manifest = plugin_root / ".claude-plugin" / "plugin.json"
        if not plugin_manifest.exists():
            continue
        try:
            plugin_data = json.loads(plugin_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        skills_rel = str(plugin_data.get("skills") or "").strip()
        if not skills_rel:
            continue
        roots.add((plugin_root / skills_rel).resolve())
    return roots


def _marketplace_bundle_roots(skill_dir: Path) -> dict[Path, set[Path]]:
    bundles: dict[Path, set[Path]] = {}
    for manifest in skill_dir.rglob("marketplace.json"):
        if manifest.parent.name != ".claude-plugin":
            continue
        bundle_root = manifest.parent.parent.resolve()
        roots = _marketplace_plugin_skill_roots(bundle_root)
        if roots:
            bundles[bundle_root] = roots
    return bundles


def _normalize_credential_spec(raw: dict) -> dict | None:
    """Normalize a credential spec dict from frontmatter metadata."""
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("key") or raw.get("name") or "").strip()
    if not key:
        return None

    def _str(name: str) -> str:
        return str(raw.get(name) or "").strip()

    def _list(name: str) -> list[str]:
        value = raw.get(name)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return _parse_yaml_list(value.strip()) or [value.strip()]
        return []

    return {
        "key": key,
        "label": _str("label") or key.replace("_", " "),
        "description": _str("description"),
        "env_vars": _list("env_vars") or _list("env"),
        "aliases": _list("aliases"),
        "docs_url": _str("docs_url") or _str("docs"),
        "manage_url": _str("manage_url") or _str("apply_url") or _str("manage"),
        "category": _str("category") or "skill",
        "supports_apply_link": bool(raw.get("supports_apply_link", True)),
    }


def _parse_credentials_block(frontmatter_text: str) -> list[dict]:
    """Parse a simple top-level YAML-ish credentials block from frontmatter."""
    creds: list[dict] = []
    in_block = False
    current: dict | None = None
    for line in frontmatter_text.splitlines():
        stripped = line.strip()
        if stripped == "credentials:":
            in_block = True
            current = None
            continue
        if not in_block:
            continue
        if stripped.startswith("credentials:"):
            inline = stripped[len("credentials:"):].strip()
            if inline.startswith("[") and inline.endswith("]"):
                try:
                    data = json.loads(inline)
                except (json.JSONDecodeError, ValueError):
                    return []
                if isinstance(data, list):
                    for item in data:
                        normalized = _normalize_credential_spec(item)
                        if normalized:
                            creds.append(normalized)
                return creds
        if stripped and not line.startswith(" ") and not line.startswith("\t"):
            break
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current:
                normalized = _normalize_credential_spec(current)
                if normalized:
                    creds.append(normalized)
            current = {}
            stripped = stripped[2:].strip()
            if ":" in stripped:
                key, value = stripped.split(":", 1)
                current[key.strip()] = value.strip().strip('"').strip("'")
            continue
        if current is None or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in {"env", "env_vars", "aliases"}:
            current[key] = _parse_yaml_list(value)
        elif key == "supports_apply_link":
            current[key] = value.lower() != "false"
        else:
            current[key] = value.strip('"').strip("'")
    if current:
        normalized = _normalize_credential_spec(current)
        if normalized:
            creds.append(normalized)
    return creds


def _parse_metadata_json(
    frontmatter_text: str,
) -> tuple[list[str], list[str], list[str], list[dict], list[dict]]:
    """
    Parse the OpenClaw new-style single-line ``metadata:`` JSON field.

    Supports::

        metadata: {"openclaw":{"requires":{"bins":["git"],"env":["TOKEN"]},"os":["darwin"]}}

    Returns ``(bins, env, os_list, install_specs, credential_specs)``.
    ``os_list`` uses OpenClaw platform names: ``darwin``, ``linux``, ``win32``.
    ``install_specs`` is the raw ``install`` array from the metadata.
    """
    bins: list[str] = []
    env: list[str] = []
    os_list: list[str] = []
    install_specs: list[dict] = []
    credential_specs: list[dict] = []

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
        raw_credentials = openclaw.get("credentials", []) if isinstance(openclaw, dict) else []
        if isinstance(raw_credentials, list):
            credential_specs = []
            for item in raw_credentials:
                normalized = _normalize_credential_spec(item)
                if normalized:
                    credential_specs.append(normalized)
        break  # only process first metadata line

    return bins, env, os_list, install_specs, credential_specs


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

    def __init__(self, skill_dirs: "Path | list[Path] | list[tuple[Path, str]]") -> None:
        # Accept three forms:
        #   Path                          → single dir, tier="user"
        #   list[Path]                    → multiple dirs, tier="user" for all
        #   list[tuple[Path, str]]        → each entry carries its own tier label
        if isinstance(skill_dirs, Path):
            self._skill_dirs: list[tuple[Path, str]] = [(skill_dirs, "user")]
        else:
            normalised = []
            for entry in skill_dirs:
                if isinstance(entry, tuple):
                    normalised.append((entry[0], entry[1]))
                else:
                    normalised.append((entry, "user"))
            self._skill_dirs = normalised
        self._skills: dict[str, dict] = {}  # name → metadata dict
        self._skill_versions: dict[str, list[dict]] = {}
        self._state_path = self._resolve_state_path()
        self._state: dict[str, dict] = self._load_state()
        self._do_load()

    def _resolve_state_path(self) -> Path | None:
        for directory, tier in reversed(self._skill_dirs):
            if tier in {"user", "workspace"} and directory:
                return directory / ".skill-state.json"
        for directory, _tier in reversed(self._skill_dirs):
            if directory:
                return directory / ".skill-state.json"
        return None

    def _load_state(self) -> dict[str, dict]:
        path = self._state_path
        if not path or not path.exists():
            return {"disabled": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {"disabled": {}}
        if not isinstance(data, dict):
            return {"disabled": {}}
        disabled = data.get("disabled")
        if not isinstance(disabled, dict):
            data["disabled"] = {}
        return data

    def _save_state(self) -> None:
        path = self._state_path
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._state, indent=2, sort_keys=True), encoding="utf-8")

    def _disabled_map(self) -> dict:
        disabled = self._state.setdefault("disabled", {})
        if not isinstance(disabled, dict):
            disabled = {}
            self._state["disabled"] = disabled
        return disabled

    def _do_load(self) -> None:
        """Clear and reload all skill directories (built-ins + configured dirs)."""
        self._skills = {}
        self._skill_versions = {}
        self._state = self._load_state()
        # Built-ins first (lowest priority)
        if _BUILTINS_DIR.exists():
            self._load(_BUILTINS_DIR, tier="builtin")
        # Configured dirs in ascending priority, each with its own tier label
        for d, tier in self._skill_dirs:
            if d:
                self._load(d, tier=tier)

    def reload(self) -> None:
        """Rescan all configured skill directories and refresh the registry.

        Call this after writing a new SKILL.md to disk so the skill is
        immediately available without a server restart.
        """
        self._do_load()

    def delete_skill(self, name: str) -> tuple[bool, str]:
        """Delete a user-installed skill. Returns (ok, error_message)."""
        skill = self._skills.get(name)
        if skill is None:
            return False, f"Skill '{name}' not found"
        tier = str(skill.get("tier") or "user")
        if tier != "user":
            return False, f"Cannot delete {tier} skill '{name}'"
        skill_path = Path(skill["path"])   # path to SKILL.md
        skill_dir  = skill_path.parent    # directory to remove
        try:
            shutil.rmtree(skill_dir)
        except OSError as exc:
            return False, str(exc)
        self._disabled_map().pop(name, None)
        self._save_state()
        self.reload()
        return True, ""

    def prune_shadowed(self, name: str) -> tuple[bool, str, list[str]]:
        """Remove inactive shadowed copies when they can be safely pruned."""
        skill = self._skills.get(name) or next(
            (s for s in self._skills.values() if s["name"].lower() == name.lower()),
            None,
        )
        if skill is None:
            return False, f"Skill '{name}' not found", []
        overrides, governance = self._override_chain(skill)
        removable = [item for item in overrides if not item.get("active") and item.get("can_prune")]
        if not removable:
            reason = governance.get("summary") or "No shadowed copies can be safely pruned."
            return False, reason, []
        removed: list[str] = []
        for item in removable:
            root = Path(str(item.get("path") or "")).parent
            if not root.exists():
                continue
            shutil.rmtree(root)
            removed.append(str(root))
        self._disabled_map().pop(name, None)
        self._save_state()
        self.reload()
        return True, "", removed

    # ------------------------------------------------------------------
    # Internal loading
    # ------------------------------------------------------------------

    _SKIP_DIRS = {"staging", "clawhub", ".git", "__pycache__", "node_modules"}

    def _load(self, skill_dir: Path, tier: str = "user") -> None:
        if not skill_dir or not skill_dir.exists():
            return
        marketplace_bundles = _marketplace_bundle_roots(skill_dir.resolve())
        for md_file in skill_dir.rglob("SKILL.md"):
            # Skip staging / scratch directories that should not be auto-loaded
            if any(part in self._SKIP_DIRS for part in md_file.parts):
                continue
            md_parent = md_file.parent.resolve()
            if md_parent in marketplace_bundles:
                # A marketplace bundle can ship a wrapper-level SKILL.md and one or
                # more plugin-scoped skills. Prefer the plugin-scoped skill roots.
                continue
            skill = self._parse(md_file, tier=tier)
            if skill["name"]:
                self._skill_versions.setdefault(skill["name"], []).append(skill)
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
        credential_specs: list[dict] = []

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
                meta_bins, meta_env, os_list, install_specs, credential_specs = _parse_metadata_json(fm)
                requires_bins.extend(meta_bins)
                requires_env.extend(meta_env)
                if not credential_specs:
                    credential_specs = _parse_credentials_block(fm)

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
            "credentials": credential_specs,
            "mtime": path.stat().st_mtime if path.exists() else 0,
            "size": path.stat().st_size if path.exists() else 0,
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
        original_available = bool(skill.get("available", True))
        original_reason = str(skill.get("reason") or "")
        if skill["content"] is None:
            skill["content"] = self._load_content(skill)
        if self._is_disabled(skill["name"]):
            loaded = dict(skill)
            loaded["available"] = False
            loaded["reason"] = "Disabled by user"
            return loaded
        skill["available"] = original_available
        skill["reason"] = original_reason
        return skill

    def _is_disabled(self, name: str) -> bool:
        return name in self._disabled_map()

    def _editable(self, skill: dict) -> bool:
        return str(skill.get("tier") or "user") in {"user", "workspace"}

    def _override_chain(self, skill: dict) -> tuple[list[dict], dict]:
        name = str(skill.get("name") or "")
        versions = self._skill_versions.get(name, [])
        active_root = Path(str(skill.get("path") or "")).parent.resolve() if skill.get("path") else None
        overrides: list[dict] = []
        prunable_shadow_count = 0
        blocked_shadow_count = 0
        for v in versions:
            path_str = str(v.get("path") or "")
            root = Path(path_str).parent.resolve() if path_str else None
            active = v is skill
            editable = self._editable(v)
            deletable = str(v.get("tier") or "user") == "user"
            can_prune = False
            prune_reason = ""
            if not active:
                if not editable:
                    prune_reason = f"{v.get('tier', 'system').title()} skill is read only."
                elif not active_root or not root:
                    prune_reason = "Missing skill path."
                elif root == active_root:
                    prune_reason = "Shares the same installed skill root as the active definition."
                elif _contains_path(root, active_root) or _contains_path(active_root, root):
                    prune_reason = "Shares a marketplace bundle with the active definition, so it cannot be safely removed alone."
                else:
                    can_prune = True
            if not active:
                if can_prune:
                    prunable_shadow_count += 1
                else:
                    blocked_shadow_count += 1
            overrides.append({
                "name": v.get("name", ""),
                "tier": v.get("tier", "user"),
                "path": path_str,
                "active": active,
                "editable": editable,
                "deletable": deletable,
                "can_prune": can_prune,
                "prune_reason": prune_reason,
            })
        governance = {
            "needs_governance": len(versions) > 1,
            "shadow_count": max(0, len(versions) - 1),
            "prunable_shadow_count": prunable_shadow_count,
            "blocked_shadow_count": blocked_shadow_count,
            "can_prune_shadowed": prunable_shadow_count > 0,
            "summary": (
                "Shadowed copies can be cleaned up."
                if prunable_shadow_count > 0
                else (
                    "Override chain detected, but the shadowed copies share a bundle with the active skill."
                    if blocked_shadow_count > 0
                    else ""
                )
            ),
        }
        return overrides, governance

    def _summarize(self, skill: dict, *, include_path: bool = False) -> dict:
        name = skill.get("name", "")
        disabled = self._is_disabled(name)
        available = bool(skill.get("available", True)) and not disabled
        reason = str(skill.get("reason") or "")
        if disabled:
            reason = "Disabled by user"
        versions = self._skill_versions.get(name, [])
        overrides, governance = self._override_chain(skill)
        install_hints = [
            {"kind": spec.get("kind", ""), "cmd": _format_install_cmd(spec)}
            for spec in skill.get("install_specs", [])
            if _format_install_cmd(spec)
        ]
        item = {
            "name":        name,
            "description": skill.get("description", ""),
            "tier":        skill.get("tier", "user"),
            "scope":       skill.get("tier", "user"),
            "builtin":     skill.get("tier") == "builtin",
            "tags":        skill.get("tags", []),
            "available":   available,
            "enabled":     not disabled,
            "disabled":    disabled,
            "reason":      reason,
            "direct_tool": skill.get("direct_tool", ""),
            "editable":    self._editable(skill),
            "deletable":   str(skill.get("tier") or "user") == "user",
            "has_conflict": len(versions) > 1,
            "override_count": max(0, len(versions) - 1),
            "overrides": overrides,
            "governance": governance,
            "mtime":       skill.get("mtime", 0),
            "size":        skill.get("size", 0),
            "author":      skill.get("author", ""),
            "version":     skill.get("version", ""),
            "license":     skill.get("license_", ""),
            "homepage":    skill.get("homepage", ""),
            "source":      skill.get("source", ""),
            "requires_bins": skill.get("requires_bins", []),
            "requires_env": skill.get("requires_env", []),
            "os_list":     skill.get("os_list", []),
            "install_hints": install_hints,
            "credentials": skill.get("credentials", []),
        }
        if include_path:
            item["path"] = skill.get("path", "")
            item["directory"] = str(Path(str(skill.get("path", ""))).parent) if skill.get("path") else ""
        return item

    def list_all(self) -> list[dict]:
        return [self._summarize(s) for s in self._skills.values()]

    def query(
        self,
        *,
        q: str = "",
        scope: str = "all",
        status: str = "all",
        sort: str = "name",
        offset: int = 0,
        limit: int = 80,
    ) -> dict:
        items = [self._summarize(s) for s in self._skills.values()]
        counts = {
            "all": len(items),
            "enabled": sum(1 for item in items if item.get("enabled")),
            "disabled": sum(1 for item in items if not item.get("enabled")),
            "unavailable": sum(1 for item in items if item.get("available") is False),
            "conflicts": sum(1 for item in items if item.get("has_conflict")),
            "builtin": sum(1 for item in items if item.get("scope") == "builtin"),
            "system": sum(1 for item in items if item.get("scope") == "system"),
            "user": sum(1 for item in items if item.get("scope") == "user"),
            "workspace": sum(1 for item in items if item.get("scope") == "workspace"),
        }
        needle = q.strip().lower()
        if needle:
            def _matches(item: dict) -> bool:
                hay = " ".join([
                    str(item.get("name", "")),
                    str(item.get("description", "")),
                    " ".join(str(t) for t in item.get("tags", [])),
                    str(item.get("author", "")),
                ]).lower()
                return needle in hay
            items = [item for item in items if _matches(item)]
        if scope and scope != "all":
            items = [item for item in items if item.get("scope") == scope]
        if status == "enabled":
            items = [item for item in items if item.get("enabled")]
        elif status == "disabled":
            items = [item for item in items if not item.get("enabled")]
        elif status == "unavailable":
            items = [item for item in items if item.get("available") is False]
        elif status == "conflicts":
            items = [item for item in items if item.get("has_conflict")]
        if sort == "updated":
            items.sort(key=lambda item: (item.get("mtime") or 0, item.get("name", "")), reverse=True)
        elif sort == "scope":
            order = {"workspace": 0, "user": 1, "system": 2, "builtin": 3}
            items.sort(key=lambda item: (order.get(str(item.get("scope")), 9), str(item.get("name", "")).lower()))
        elif sort == "status":
            items.sort(key=lambda item: (item.get("available") is not True, item.get("enabled") is not True, str(item.get("name", "")).lower()))
        else:
            items.sort(key=lambda item: str(item.get("name", "")).lower())
        total = len(items)
        offset = max(0, int(offset or 0))
        limit = max(1, min(300, int(limit or 80)))
        return {
            "items": items[offset:offset + limit],
            "total": total,
            "offset": offset,
            "limit": limit,
            "counts": counts,
        }

    def search(
        self,
        q: str,
        *,
        scope: str = "all",
        status: str = "available",
        limit: int = 10,
    ) -> dict:
        """Return compact, ranked skill candidates for task-time routing."""
        items = [self._summarize(s) for s in self._skills.values()]
        if scope and scope != "all":
            items = [item for item in items if item.get("scope") == scope]
        if status == "available":
            items = [
                item for item in items
                if item.get("enabled", True) and item.get("available", True)
            ]
        elif status == "enabled":
            items = [item for item in items if item.get("enabled", True)]
        elif status == "all":
            pass
        else:
            items = [item for item in items if item.get("available", True)]

        query = " ".join(str(q or "").split())
        tokens = _search_tokens(query)
        limit = max(1, min(50, int(limit or 10)))
        scope_boost = {"workspace": 6, "user": 4, "system": 2, "builtin": 1}

        def _score(item: dict) -> tuple[int, str]:
            name = str(item.get("name") or "").lower()
            direct_tool = str(item.get("direct_tool") or "").lower()
            description = str(item.get("description") or "").lower()
            tags = [str(tag).lower() for tag in item.get("tags") or [] if tag]
            tag_text = " ".join(tags)
            haystack = " ".join([name, direct_tool, description, tag_text])
            score = int(scope_boost.get(str(item.get("scope") or item.get("tier")), 0))

            if query:
                q_lower = query.lower()
                if q_lower == name or q_lower == direct_tool:
                    score += 100
                elif q_lower in name:
                    score += 60
                elif direct_tool and q_lower in direct_tool:
                    score += 55
                elif any(q_lower == tag for tag in tags):
                    score += 50
                elif q_lower in description:
                    score += 25

            for token in tokens:
                if token == name or token == direct_tool:
                    score += 40
                elif token in name:
                    score += 18
                elif direct_tool and token in direct_tool:
                    score += 16
                elif token in tags:
                    score += 14
                elif token in description:
                    score += 6
                elif token in haystack:
                    score += 2

            if not query:
                score += int(item.get("mtime") or 0) // 1_000_000_000
            return score, name

        scored: list[tuple[int, str, dict]] = []
        for item in items:
            score, name = _score(item)
            if tokens and score <= scope_boost.get(str(item.get("scope") or item.get("tier")), 0):
                continue
            compact = {
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "scope": item.get("scope", item.get("tier", "user")),
                "tags": item.get("tags", []),
                "direct_tool": item.get("direct_tool", ""),
                "available": item.get("available", True),
                "enabled": item.get("enabled", True),
                "score": score,
            }
            scored.append((score, name, compact))
        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        return {
            "items": [entry[2] for entry in scored[:limit]],
            "total": len(scored),
            "query": query,
            "limit": limit,
        }

    def detail(self, name: str) -> dict | None:
        skill = self._skills.get(name) or next(
            (s for s in self._skills.values() if s["name"].lower() == name.lower()),
            None,
        )
        if skill is None:
            return None
        item = self._summarize(skill, include_path=True)
        content = self._load_content(skill)
        item["content_preview"] = content[:6000]
        item["content_length"] = len(content)
        return item

    def credential_specs(self) -> list[dict]:
        """Return unique credential specs declared by active skills."""
        merged: dict[str, dict] = {}
        for skill in self._skills.values():
            skill_name = str(skill.get("name") or "")
            for raw in skill.get("credentials", []) or []:
                if not isinstance(raw, dict):
                    continue
                key = str(raw.get("key") or "").strip()
                if not key:
                    continue
                merged[key] = {
                    **raw,
                    "key": key,
                    "label": str(raw.get("label") or key.replace("_", " ")).strip(),
                    "description": str(raw.get("description") or "").strip(),
                    "category": str(raw.get("category") or "skill").strip() or "skill",
                    "source_skill": skill_name,
                }
        return list(merged.values())

    def health(self) -> dict:
        items = []
        for skill in self._skills.values():
            item = self._summarize(skill, include_path=True)
            problems: list[str] = []
            path = Path(str(skill.get("path", "")))
            if not path.exists():
                problems.append("SKILL.md is missing")
            if not item.get("enabled"):
                problems.append("Disabled")
            if not skill.get("available", True):
                problems.append(str(skill.get("reason") or "Requirements not met"))
            if item.get("has_conflict"):
                problems.append(f"Overridden by {item.get('override_count')} other definition(s)")
            item["ok"] = not problems
            item["problems"] = problems
            items.append(item)
        return {
            "checked_at": time(),
            "ok": all(item.get("ok") for item in items),
            "items": sorted(items, key=lambda item: (item.get("ok") is True, str(item.get("name", "")).lower())),
            "summary": {
                "total": len(items),
                "ok": sum(1 for item in items if item.get("ok")),
                "issues": sum(1 for item in items if not item.get("ok")),
            },
        }

    def set_enabled(self, name: str, enabled: bool) -> tuple[bool, str, dict | None]:
        skill = self._skills.get(name) or next(
            (s for s in self._skills.values() if s["name"].lower() == name.lower()),
            None,
        )
        if skill is None:
            return False, f"Skill '{name}' not found", None
        if not self._editable(skill):
            return False, f"Cannot change enabled state for {skill.get('tier', 'system')} skill '{name}'", None
        disabled = self._disabled_map()
        if enabled:
            disabled.pop(skill["name"], None)
        else:
            disabled[skill["name"]] = {
                "name": skill["name"],
                "tier": skill.get("tier", "user"),
                "path": skill.get("path", ""),
                "updated_at": time(),
            }
        self._save_state()
        return True, "", self._summarize(skill)

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
            "mtime": Path(path).stat().st_mtime if Path(path).exists() else 0,
            "size": Path(path).stat().st_size if Path(path).exists() else 0,
        }
        self._skill_versions.setdefault(name, []).append(self._skills[name])

    def __len__(self) -> int:
        return len(self._skills)
