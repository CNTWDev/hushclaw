"""install_skill — built-in tool for installing HushClaw skills from any source.

Supports: local directory, local ZIP, HTTPS ZIP URL, Git repository URL.
Runs the full post-install pipeline: pip deps, registry reload, tool plugin
loading, and .skill-lock.json update.

Invariant: only imports from hushclaw.tools.base (no other hushclaw.* imports).
All helper logic is inlined.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from hushclaw.tools.base import ToolResult, tool


# ---------------------------------------------------------------------------
# Helpers (inlined — no hushclaw.* imports)
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _find_skill_root(extracted_dir: Path) -> Path | None:
    """Return the directory that directly contains SKILL.md.

    Handles nested ZIP layouts like ``repo-name-1.0.3/SKILL.md`` where the
    archive root is one level above the actual skill directory.
    """
    if (extracted_dir / "SKILL.md").exists():
        return extracted_dir
    # One level deep (GitHub archive format, version-suffixed dirs, etc.)
    for child in sorted(extracted_dir.iterdir()):
        if child.is_dir() and (child / "SKILL.md").exists():
            return child
    return None


def _parse_skill_frontmatter(skill_md_path: Path) -> dict:
    """Return dict with at least ``name`` and ``version`` from SKILL.md frontmatter."""
    result: dict = {"name": "", "version": ""}
    try:
        text = skill_md_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return result
    if not text.startswith("---"):
        return result
    parts = text.split("---", 2)
    if len(parts) < 2:
        return result
    for line in parts[1].splitlines():
        ls = line.strip()
        if ls.startswith("name:"):
            result["name"] = ls[5:].strip().strip('"').strip("'")
        elif ls.startswith("version:"):
            result["version"] = ls[8:].strip().strip('"').strip("'")
        elif ls.startswith("metadata:"):
            raw_json = ls[9:].strip()
            try:
                result["_metadata_json"] = json.loads(raw_json)
            except (json.JSONDecodeError, ValueError):
                pass
        elif ls == "requires:":
            result["_in_requires"] = True
        elif result.get("_in_requires") and ls.startswith("bins:"):
            result["_legacy_bins"] = _parse_yaml_list(ls[5:].strip())
        elif result.get("_in_requires") and ls.startswith("env:"):
            result["_legacy_env"] = _parse_yaml_list(ls[4:].strip())
        elif ls and ":" in ls and not line.startswith(" ") and not line.startswith("\t"):
            result.pop("_in_requires", None)
    return result


def _parse_yaml_list(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        items = [s.strip().strip('"').strip("'") for s in inner.split(",")]
        return [i for i in items if i]
    return []


def _check_compatibility(skill_md_path: Path) -> list[str]:
    """Return human-readable compatibility warnings (empty list = all good)."""
    warnings: list[str] = []
    fm = _parse_skill_frontmatter(skill_md_path)

    bins: list[str] = list(fm.get("_legacy_bins") or [])
    env_vars: list[str] = list(fm.get("_legacy_env") or [])
    os_list: list[str] = []

    meta = fm.get("_metadata_json")
    if isinstance(meta, dict):
        openclaw = meta.get("openclaw") or meta.get("clawdbot") or {}
        if isinstance(openclaw, dict):
            requires = openclaw.get("requires", {})
            if isinstance(requires, dict):
                raw_bins = requires.get("bins", [])
                raw_env = requires.get("env", [])
                if isinstance(raw_bins, list):
                    bins.extend(str(b) for b in raw_bins)
                if isinstance(raw_env, list):
                    env_vars.extend(str(e) for e in raw_env)
            raw_os = openclaw.get("os", [])
            if isinstance(raw_os, list):
                os_list = [str(o) for o in raw_os]

    if os_list and sys.platform not in os_list:
        warnings.append(
            f"OS mismatch: skill requires {os_list}, this machine is {sys.platform!r}. "
            "The skill may not work correctly."
        )

    for b in dict.fromkeys(bins):  # deduplicate, preserve order
        if shutil.which(b) is None:
            warnings.append(
                f"Missing binary: {b!r} is required but not found on PATH. "
                f"Install it (e.g. `brew install {b}` on macOS)."
            )

    for e in dict.fromkeys(env_vars):
        if not os.environ.get(e):
            warnings.append(
                f"Missing env var: {e!r} is required but not set. "
                f"Add it to your shell config or hushclaw environment."
            )

    return warnings


def _write_lockfile(
    install_dir: Path,
    slug: str,
    source: str,
    source_type: str,
    version: str,
) -> None:
    lock_path = install_dir / ".skill-lock.json"
    lock: dict = {}
    if lock_path.exists():
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    lock[slug] = {
        "source": source,
        "source_type": source_type,
        "version": version,
        "installed_at": int(time.time()),
    }
    lock_path.write_text(json.dumps(lock, indent=2), encoding="utf-8")


def _detect_source_type(source: str) -> str:
    """Return one of: local_dir, local_zip, git, https_zip."""
    s = source.strip()
    if s.startswith("http://") or s.startswith("https://"):
        # Git: .git suffix, or github.com URL that isn't a .zip archive link
        if (
            s.rstrip("/").endswith(".git")
            or (
                ("github.com" in s or "gitlab.com" in s or "bitbucket.org" in s)
                and not s.lower().endswith(".zip")
            )
        ):
            return "git"
        return "https_zip"
    p = Path(s).expanduser()
    if p.suffix.lower() == ".zip":
        return "local_zip"
    return "local_dir"


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

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
    _config=None,
    _skill_registry=None,
    _gateway=None,
) -> ToolResult:
    # ── 1. Resolve install directory ─────────────────────────────────────────
    install_dir: Path | None = None
    if _config is not None:
        cfg_user = getattr(getattr(_config, "tools", None), "user_skill_dir", None)
        cfg_sys  = getattr(getattr(_config, "tools", None), "skill_dir", None)
        install_dir = cfg_user or cfg_sys
    if install_dir is None:
        return ToolResult.error(
            "No skill directory configured. "
            "Set tools.user_skill_dir in your hushclaw config and restart."
        )
    install_dir = Path(install_dir)
    install_dir.mkdir(parents=True, exist_ok=True)

    # ── 2. Detect source type ─────────────────────────────────────────────────
    source = source.strip()
    source_type = _detect_source_type(source)
    temp_dir: Path | None = None

    try:
        # ── 3. Acquire raw skill directory ─────────────────────────────────────
        raw_dir: Path

        if source_type == "local_dir":
            raw_dir = Path(source).expanduser().resolve()
            if not raw_dir.is_dir():
                return ToolResult.error(f"Directory not found: {raw_dir}")

        elif source_type == "local_zip":
            zip_path = Path(source).expanduser().resolve()
            if not zip_path.exists():
                return ToolResult.error(f"ZIP file not found: {zip_path}")
            if not zipfile.is_zipfile(zip_path):
                return ToolResult.error(f"Not a valid ZIP file: {zip_path}")
            temp_dir = Path(tempfile.mkdtemp(prefix="hushclaw-skill-"))
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(temp_dir)
            found = _find_skill_root(temp_dir)
            if found is None:
                return ToolResult.error(
                    "No SKILL.md found inside the ZIP. "
                    "Is this a valid HushClaw skill package?"
                )
            raw_dir = found

        elif source_type == "https_zip":
            temp_dir = Path(tempfile.mkdtemp(prefix="hushclaw-skill-"))
            zip_file = temp_dir / "download.zip"
            req = urllib.request.Request(
                source,
                headers={"User-Agent": "hushclaw-skill-installer/1.0"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                zip_file.write_bytes(resp.read())
            if not zipfile.is_zipfile(zip_file):
                return ToolResult.error(
                    f"Downloaded file from {source} is not a valid ZIP."
                )
            extract_dir = temp_dir / "extracted"
            with zipfile.ZipFile(zip_file) as zf:
                zf.extractall(extract_dir)
            found = _find_skill_root(extract_dir)
            if found is None:
                return ToolResult.error(
                    "No SKILL.md found in the downloaded ZIP. "
                    "Is this a valid HushClaw skill package?"
                )
            raw_dir = found

        else:  # git
            repo_name = source.rstrip("/").rstrip(".git").rsplit("/", 1)[-1]
            slug = _slugify(skill_name or repo_name)
            if not re.match(r"^[a-z0-9][a-z0-9_-]*$", slug):
                return ToolResult.error(
                    f"Cannot derive a valid slug from the URL. "
                    f"Pass skill_name= explicitly."
                )
            target_dir = install_dir / slug
            if (target_dir / ".git").exists():
                git_cmd = ["git", "pull"]
                cwd = str(target_dir)
            else:
                git_cmd = ["git", "clone", "--depth=1", source, str(target_dir)]
                cwd = str(install_dir)
            proc = subprocess.run(
                git_cmd, capture_output=True, text=True, cwd=cwd, timeout=120
            )
            if proc.returncode != 0:
                return ToolResult.error(
                    f"git failed (exit {proc.returncode}): {proc.stderr.strip()}"
                )
            raw_dir = target_dir

        # ── 4. Validate SKILL.md ──────────────────────────────────────────────
        skill_md = raw_dir / "SKILL.md"
        if not skill_md.exists():
            return ToolResult.error(
                f"No SKILL.md found in {raw_dir}. "
                "A valid HushClaw skill package must contain a SKILL.md file at its root."
            )

        # ── 5. Parse frontmatter for name / version ───────────────────────────
        fm = _parse_skill_frontmatter(skill_md)
        name_from_md = fm.get("name", "")
        version      = fm.get("version", "")
        slug = _slugify(skill_name or name_from_md or raw_dir.name)
        if not slug:
            return ToolResult.error(
                "Cannot determine a slug for this skill. "
                "Add a `name:` field to SKILL.md or pass skill_name= explicitly."
            )

        # ── 6. Compatibility check ────────────────────────────────────────────
        compat_warnings = _check_compatibility(skill_md)

        # ── 7. Copy to install directory (skip for git — already in place) ────
        target_dir = install_dir / slug
        if source_type != "git":
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(raw_dir, target_dir)

        # ── 8. pip install requirements.txt ──────────────────────────────────
        deps_ok: bool | None = None
        deps_error = ""
        req_file = target_dir / "requirements.txt"
        if req_file.exists():
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_file), "-q"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0:
                deps_ok = True
            else:
                deps_ok = False
                deps_error = (proc.stderr or proc.stdout or "").strip()[:600]

        # ── 9. Reload skill registry ──────────────────────────────────────────
        if _skill_registry is not None:
            _skill_registry.reload()

        # ── 10. Load bundled tool plugins ─────────────────────────────────────
        tools_loaded = 0
        tools_dir = target_dir / "tools"
        if tools_dir.exists():
            agent = None
            if _gateway is not None:
                agent = getattr(_gateway, "base_agent", None)
            if agent is not None and hasattr(agent, "registry"):
                agent.registry.load_plugins(tools_dir, namespace=slug)
                tools_loaded = sum(
                    1 for f in tools_dir.glob("*.py")
                    if not f.name.startswith("_")
                )

        # ── 11. Write lockfile ────────────────────────────────────────────────
        _write_lockfile(install_dir, slug, source, source_type, version)

        # ── 12. Return report ─────────────────────────────────────────────────
        return ToolResult.ok(json.dumps({
            "ok": True,
            "slug": slug,
            "name": name_from_md or slug,
            "version": version or None,
            "install_dir": str(target_dir),
            "tools_loaded": tools_loaded,
            "deps_installed": deps_ok,
            "deps_error": deps_error or None,
            "compatibility_warnings": compat_warnings,
        }, indent=2, ensure_ascii=False))

    except subprocess.TimeoutExpired:
        return ToolResult.error("Installation timed out after 120 seconds.")
    except OSError as exc:
        return ToolResult.error(f"File system error: {exc}")
    except Exception as exc:  # noqa: BLE001
        return ToolResult.error(f"Installation failed: {exc}")
    finally:
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
