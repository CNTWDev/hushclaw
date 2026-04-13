"""SkillInstaller: source routing + post-install pipeline.

This module is the single source of truth for all skill installation logic.
It replaces the ``post_install()`` function that previously lived in
``server/skill_handler.py`` and the inlined logic in ``skill_install_tool.py``.

Callers:
  - hushclaw/skills/manager.py  (SkillManager, injected into agent tools)
  - hushclaw/server/skill_handler.py  (WebUI install / import flows)

The ``on_progress`` parameter accepts an async callable ``async def f(msg: str)``
for streaming progress messages (used by WebSocket handlers).  Pass ``None``
when called from a tool (results returned in the final InstallResult instead).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger("hushclaw.skills.installer")


# ---------------------------------------------------------------------------
# Lock file helpers  (moved from server/skill_handler.py)
# ---------------------------------------------------------------------------

def read_lock(skill_dir: Path) -> dict:
    """Read .skill-lock.json from *skill_dir*. Returns {} on any error."""
    lock_path = skill_dir / ".skill-lock.json"
    try:
        if lock_path.exists():
            return json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.debug("Could not read lockfile %s: %s", lock_path, exc)
    return {}


def write_lock(skill_dir: Path, slug: str, entry: dict) -> None:
    """Upsert one *slug* entry in .skill-lock.json."""
    lock_path = skill_dir / ".skill-lock.json"
    data = read_lock(skill_dir)
    data[slug] = entry
    try:
        lock_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Could not write lockfile %s: %s", lock_path, exc)


# ---------------------------------------------------------------------------
# InstallResult
# ---------------------------------------------------------------------------

@dataclass
class InstallResult:
    """Structured result returned by SkillInstaller.install() / post_install()."""
    ok: bool
    slug: str = ""
    name: str = ""
    version: str = ""
    install_dir: str = ""
    tools_loaded: int = 0
    skill_count: int = 0          # total skills in registry after install
    repo_skill_count: int = 0     # skills found in the installed directory
    deps_installed: "bool | None" = None
    deps_error: "str | None" = None
    compatibility_warnings: list[str] = field(default_factory=list)
    error: str = ""
    warning: str = ""             # non-fatal warning (e.g. no SKILL.md found)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    def to_ws_result(self, url: str = "", repo: str = "") -> dict:
        """Format as a ``skill_install_result`` WebSocket message."""
        d: dict = {
            "type":              "skill_install_result",
            "ok":                self.ok,
            "slug":              self.slug,
            "skill_count":       self.skill_count,
            "repo_skill_count":  self.repo_skill_count,
            "bundled_tool_count": self.tools_loaded,
            "deps_installed":    self.deps_installed,
            "deps_error":        self.deps_error or "",
            "warning":           self.warning,
        }
        if not self.ok:
            d["error"] = self.error
        if url:
            d["url"] = url
        if repo:
            d["repo"] = repo
        return d


# ---------------------------------------------------------------------------
# SkillInstaller
# ---------------------------------------------------------------------------

class SkillInstaller:
    """Source-routing + post-install pipeline for HushClaw skills.

    Stateless: instantiate once (e.g. in SkillManager) and reuse.
    """

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def slugify(name: str) -> str:
        """Convert a skill name to a filesystem-safe slug."""
        slug = name.lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        return slug.strip("-")

    @staticmethod
    def detect_source_type(source: str) -> str:
        """Classify the install source.

        Returns one of: ``local_dir``, ``local_zip``, ``git``, ``https_zip``.
        """
        s = source.strip()
        if s.startswith("http://") or s.startswith("https://"):
            if (
                s.rstrip("/").endswith(".git")
                or (
                    any(h in s for h in ("github.com", "gitlab.com", "bitbucket.org"))
                    and not s.lower().endswith(".zip")
                )
            ):
                return "git"
            return "https_zip"
        p = Path(s).expanduser()
        if p.suffix.lower() == ".zip":
            return "local_zip"
        return "local_dir"

    @staticmethod
    def find_skill_root(extracted_dir: Path) -> "Path | None":
        """Return the directory that directly contains SKILL.md.

        Handles nested archive layouts, e.g. ``repo-name-1.0/SKILL.md``
        where the top-level ZIP directory is one level above the skill.
        """
        if (extracted_dir / "SKILL.md").exists():
            return extracted_dir
        for child in sorted(extracted_dir.iterdir()):
            if child.is_dir() and (child / "SKILL.md").exists():
                return child
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def install(
        self,
        source: str,
        install_dir: Path,
        slug: str | None = None,
        skill_registry=None,
        tool_registry=None,
        gateway=None,
        on_progress: "Callable[[str], Awaitable] | None" = None,
    ) -> InstallResult:
        """Unified install entry point.

        Routes to the appropriate acquisition method based on source type,
        then runs ``post_install()``.
        """
        source = source.strip()
        source_type = self.detect_source_type(source)
        temp_dir: "Path | None" = None

        try:
            # ── Acquire raw skill directory ────────────────────────────────────
            if source_type == "local_dir":
                raw_dir = Path(source).expanduser().resolve()
                if not raw_dir.is_dir():
                    return InstallResult(ok=False, error=f"Directory not found: {raw_dir}")

            elif source_type == "local_zip":
                zip_path = Path(source).expanduser().resolve()
                if not zip_path.exists():
                    return InstallResult(ok=False, error=f"ZIP file not found: {zip_path}")
                if not zipfile.is_zipfile(zip_path):
                    return InstallResult(ok=False, error=f"Not a valid ZIP file: {zip_path}")
                temp_dir = Path(tempfile.mkdtemp(prefix="hc-skill-"))
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(temp_dir)
                raw_dir = self.find_skill_root(temp_dir)
                if raw_dir is None:
                    return InstallResult(
                        ok=False,
                        error="No SKILL.md found in the ZIP. Is this a valid HushClaw skill package?",
                    )

            elif source_type == "https_zip":
                if on_progress:
                    await on_progress("Downloading…")
                temp_dir = Path(tempfile.mkdtemp(prefix="hc-skill-"))
                zip_file = temp_dir / "download.zip"
                req = urllib.request.Request(
                    source, headers={"User-Agent": "hushclaw-skill-installer/1.0"}
                )
                loop = asyncio.get_event_loop()
                try:
                    from hushclaw.util.ssl_context import make_ssl_context as _ssl
                    ssl_ctx = _ssl()
                    raw_bytes = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda: urllib.request.urlopen(req, timeout=60, context=ssl_ctx).read(),
                        ),
                        timeout=65,
                    )
                except asyncio.TimeoutError:
                    return InstallResult(ok=False, error="Download timed out after 60 seconds.")
                zip_file.write_bytes(raw_bytes)
                if not zipfile.is_zipfile(zip_file):
                    return InstallResult(ok=False, error="Downloaded file is not a valid ZIP.")
                extract_dir = temp_dir / "extracted"
                with zipfile.ZipFile(zip_file) as zf:
                    zf.extractall(extract_dir)
                raw_dir = self.find_skill_root(extract_dir)
                if raw_dir is None:
                    return InstallResult(
                        ok=False,
                        error="No SKILL.md found in the downloaded ZIP.",
                    )

            else:  # git
                repo_name = source.rstrip("/").rstrip(".git").rsplit("/", 1)[-1]
                derived_slug = slug or self.slugify(repo_name)
                target_dir = install_dir / derived_slug
                if on_progress:
                    action = "Updating" if (target_dir / ".git").exists() else "Cloning"
                    await on_progress(f"{action} {repo_name}…")

                if (target_dir / ".git").exists():
                    git_cmd = ["git", "-C", str(target_dir), "pull"]
                    cwd = str(target_dir)
                else:
                    git_cmd = ["git", "clone", "--depth=1", source, str(target_dir)]
                    cwd = str(install_dir)

                proc = await asyncio.create_subprocess_exec(
                    *git_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                try:
                    _, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=120)
                except asyncio.TimeoutError:
                    proc.kill()
                    return InstallResult(ok=False, error="Git operation timed out after 120 seconds.")

                if proc.returncode != 0:
                    lines = stderr_b.decode(errors="ignore").strip().splitlines()
                    err = lines[-1] if lines else "Unknown git error"
                    return InstallResult(ok=False, error=err)

                return await self.post_install(
                    target_dir=target_dir,
                    slug=derived_slug,
                    source=source,
                    source_type="git",
                    install_dir=install_dir,
                    skill_registry=skill_registry,
                    tool_registry=tool_registry,
                    gateway=gateway,
                    on_progress=on_progress,
                )

            # ── Validate SKILL.md ──────────────────────────────────────────────
            skill_md = raw_dir / "SKILL.md"
            if not skill_md.exists():
                return InstallResult(
                    ok=False,
                    error=(
                        f"No SKILL.md found in {raw_dir.name}. "
                        "A valid HushClaw skill package must have SKILL.md at its root."
                    ),
                )

            # ── Parse name / version from frontmatter ──────────────────────────
            from hushclaw.skills.validator import SkillValidator
            validator = SkillValidator()
            fm = validator.parse_frontmatter(skill_md)
            name_from_md = fm.get("name", "")
            version      = fm.get("version", "")
            derived_slug = slug or self.slugify(name_from_md or raw_dir.name)
            if not derived_slug:
                return InstallResult(
                    ok=False,
                    error="Cannot determine skill slug. Add `name:` to SKILL.md or pass skill_name=.",
                )

            # ── Compatibility check (non-blocking) ─────────────────────────────
            compat = validator.check_compatibility(skill_md)

            # ── Copy to install directory ──────────────────────────────────────
            target_dir = install_dir / derived_slug
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(raw_dir, target_dir)

            result = await self.post_install(
                target_dir=target_dir,
                slug=derived_slug,
                source=source,
                source_type=source_type,
                install_dir=install_dir,
                skill_registry=skill_registry,
                tool_registry=tool_registry,
                gateway=gateway,
                on_progress=on_progress,
            )
            result.name = name_from_md or derived_slug
            result.compatibility_warnings = compat.all_warnings
            return result

        except Exception as exc:  # noqa: BLE001
            log.error("SkillInstaller.install error: %s", exc, exc_info=True)
            return InstallResult(ok=False, error=str(exc))
        finally:
            if temp_dir is not None and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    async def post_install(
        self,
        target_dir: Path,
        slug: str,
        source: str,
        source_type: str,
        install_dir: Path,
        skill_registry=None,
        tool_registry=None,
        gateway=None,
        on_progress: "Callable[[str], Awaitable] | None" = None,
        namespace: "str | None" = ...,  # type: ignore[assignment]
    ) -> InstallResult:
        """Run the post-copy install pipeline.

        Steps:
        1. pip install requirements.txt (async subprocess, 120 s timeout)
        2. skill_registry.reload() — in-place; ensures install_dir is scanned
        3. gateway.clear_all_cached_loops() if gateway is not None
        4. tool_registry.load_plugins(tools_dir, namespace=...)
        5. write_lock()

        ``namespace``: if ``...`` (the default sentinel), auto-determine:
          system skills (in skill_dir) get no namespace; user skills get slug.
          Pass ``None`` to force no namespace, or a string to force a specific one.
        """
        # ----- 1. pip install ------------------------------------------------
        deps_ok: "bool | None" = None
        deps_error = ""
        req_file = target_dir / "requirements.txt"
        if req_file.exists():
            if on_progress:
                await on_progress("Installing dependencies from requirements.txt…")
            pip_proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "-r", str(req_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr_b = await asyncio.wait_for(pip_proc.communicate(), timeout=120)
                deps_ok = (pip_proc.returncode == 0)
                if not deps_ok:
                    deps_error = stderr_b.decode(errors="ignore").strip()[-800:]
            except asyncio.TimeoutError:
                pip_proc.kill()
                deps_ok = False
                deps_error = "pip install timed out after 120 seconds."
                log.warning("pip install timed out for %s", req_file)

        # ----- 2. Reload skill registry --------------------------------------
        if skill_registry is not None:
            # Ensure the install directory is in the registry's scan list
            # (handles the case where user_skill_dir was created for the first time).
            if hasattr(skill_registry, "_skill_dirs") and install_dir not in skill_registry._skill_dirs:
                skill_registry._skill_dirs.append(install_dir)
            skill_registry.reload()

        # Count skills from this specific install (used for the WS result)
        repo_skill_count = 0
        if skill_registry is not None and hasattr(skill_registry, "_skills"):
            repo_skill_count = sum(
                1 for s in skill_registry._skills.values()
                if str(target_dir) in s.get("path", "")
            )

        warning = ""
        if repo_skill_count == 0:
            warning = (
                "No SKILL.md files found in this directory. "
                "It may not be a valid skill package. "
                "Check for a SKILL.md at the repository root."
            )

        # ----- 3. Clear cached loops so next request picks up new tools ------
        if gateway is not None and hasattr(gateway, "clear_all_cached_loops"):
            gateway.clear_all_cached_loops()

        # ----- 4. Load bundled tool plugins ----------------------------------
        bundled_tool_count = 0
        tools_dir = target_dir / "tools"
        if tools_dir.is_dir() and any(tools_dir.glob("*.py")) and tool_registry is not None:
            # Auto-determine namespace if not explicitly passed
            if namespace is ...:  # type: ignore[comparison-overlap]
                namespace = slug  # user skills always get a namespace
            before = len(tool_registry)
            tool_registry.load_plugins(tools_dir, namespace=namespace)
            bundled_tool_count = len(tool_registry) - before

        # ----- 5. Read version from SKILL.md ---------------------------------
        installed_version = ""
        skill_md = target_dir / "SKILL.md"
        if skill_md.exists():
            try:
                for line in skill_md.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("version:"):
                        installed_version = (
                            line.split(":", 1)[1].strip().strip('"').strip("'")
                        )
                        break
            except OSError:
                pass

        # ----- 6. Write lockfile ---------------------------------------------
        write_lock(install_dir, slug, {
            "source":      source,
            "source_type": source_type,
            "version":     installed_version,
            "installed_at": int(time.time()),
        })

        skill_count = len(skill_registry) if skill_registry is not None else 0

        return InstallResult(
            ok=True,
            slug=slug,
            name=slug,          # caller overwrites with parsed name if available
            version=installed_version,
            install_dir=str(target_dir),
            tools_loaded=bundled_tool_count,
            skill_count=skill_count,
            repo_skill_count=repo_skill_count,
            deps_installed=deps_ok,
            deps_error=deps_error or None,
            warning=warning,
        )
