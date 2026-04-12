"""Skill management handler functions — extracted from server.py.

Handles list_skills, save_skill, delete_skill, install_skill_repo,
install_skill_zip, export_skills, and import_skill_zip WebSocket messages.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger("hushclaw.server.skills")

# ---------------------------------------------------------------------------
# Lock file helpers
# ---------------------------------------------------------------------------

def read_lock(skill_dir: Path) -> dict:
    """Read .skill-lock.json from skill_dir. Returns {} on any error."""
    lock_path = skill_dir / ".skill-lock.json"
    try:
        if lock_path.exists():
            return json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.debug("Could not read lockfile %s: %s", lock_path, exc)
    return {}


def write_lock(skill_dir: Path, slug: str, entry: dict) -> None:
    """Upsert one slug entry in .skill-lock.json."""
    lock_path = skill_dir / ".skill-lock.json"
    data = read_lock(skill_dir)
    data[slug] = entry
    try:
        lock_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Could not write lockfile %s: %s", lock_path, exc)


# ---------------------------------------------------------------------------
# Post-install processing
# ---------------------------------------------------------------------------

async def post_install(
    ws,
    target_dir: Path,
    slug: str,
    source: str,
    source_type: str,
    agent,
    install_skill_dir: Path,
) -> dict:
    """Shared post-download processing for both git and zip installs.

    1. pip install requirements.txt
    2. SkillRegistry reload
    3. load_plugins with correct namespace
    4. Write .skill-lock.json entry
    Returns a result dict (to be sent as skill_install_result).
    """
    from hushclaw.skills.loader import SkillRegistry

    # ----- 1. pip dependencies -----------------------------------------------
    deps_ok: bool | None = None
    deps_error = ""
    req_file = target_dir / "requirements.txt"
    if req_file.exists():
        await ws.send(json.dumps({
            "type": "skill_install_progress",
            "slug": slug,
            "message": "Installing dependencies from requirements.txt…",
        }))
        pip_proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "-r", str(req_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                pip_proc.communicate(), timeout=120
            )
            deps_ok = (pip_proc.returncode == 0)
            if not deps_ok:
                deps_error = stderr_b.decode(errors="ignore").strip()[-800:]
        except asyncio.TimeoutError:
            pip_proc.kill()
            deps_ok = False
            deps_error = "pip install timed out after 120 seconds."
            log.warning("pip install timed out for %s", req_file)

    # ----- 2. SkillRegistry reload -------------------------------------------
    skill_dirs = []
    if agent.config.tools.skill_dir:
        skill_dirs.append(agent.config.tools.skill_dir)
    if (
        agent.config.tools.user_skill_dir
        and agent.config.tools.user_skill_dir.exists()
    ):
        skill_dirs.append(agent.config.tools.user_skill_dir)
    if not skill_dirs:
        skill_dirs.append(install_skill_dir)
    agent._skill_registry = SkillRegistry(skill_dirs)
    # Clear all cached AgentLoop objects so next request gets a fresh loop
    # that picks up the updated _skill_registry (loops cache it at creation).
    if hasattr(agent, "_gateway") and agent._gateway is not None:
        agent._gateway.clear_all_cached_loops()

    # Count skills from this specific install directory
    repo_skill_count = sum(
        1 for s in agent._skill_registry._skills.values()
        if str(target_dir) in s.get("path", "")
    )
    warning = ""
    if repo_skill_count == 0:
        warning = (
            "No SKILL.md files found in this directory. "
            "It may not be a skill package. "
            "Check for a SKILL.md file in the repository root."
        )

    # ----- 3. Load bundled tools (namespace consistency) ---------------------
    bundled_tool_count = 0
    tools_dir = target_dir / "tools"
    if tools_dir.is_dir() and any(tools_dir.glob("*.py")):
        before = len(agent.registry)
        is_system = (
            agent.config.tools.skill_dir
            and str(target_dir).startswith(str(agent.config.tools.skill_dir))
        )
        ns = None if is_system else slug
        agent.registry.load_plugins(tools_dir, namespace=ns)
        bundled_tool_count = len(agent.registry) - before

    # ----- 4. Write lockfile -------------------------------------------------
    skill_md = target_dir / "SKILL.md"
    installed_version = ""
    if skill_md.exists():
        try:
            content = skill_md.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.strip().startswith("version:"):
                    installed_version = line.split(":", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception:
            pass
    write_lock(install_skill_dir, slug, {
        "source": source,
        "source_type": source_type,
        "version": installed_version,
        "installed_at": int(time.time()),
    })

    return {
        "type": "skill_install_result",
        "ok": True,
        "slug": slug,
        "skill_count": len(agent._skill_registry),
        "repo_skill_count": repo_skill_count,
        "bundled_tool_count": bundled_tool_count,
        "deps_installed": deps_ok,
        "deps_error": deps_error,
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# Handler functions
# ---------------------------------------------------------------------------

async def handle_list_skills(ws, gateway) -> None:
    agent = gateway.base_agent
    registry = getattr(agent, "_skill_registry", None)
    items = registry.list_all() if registry else []
    skills_raw = getattr(registry, "_skills", {}) if registry else {}

    # Merge installed_version from lockfile(s)
    lock: dict = {}
    for skill_dir_path in [agent.config.tools.skill_dir, agent.config.tools.user_skill_dir]:
        if skill_dir_path and skill_dir_path.exists():
            lock.update(read_lock(skill_dir_path))
    if lock:
        for item in items:
            entry = lock.get(item["name"])
            if entry:
                item["installed_version"] = entry.get("version", "")
                item["installed_at"] = entry.get("installed_at", 0)

    skill_dir_path = agent.config.tools.skill_dir.resolve() if agent.config.tools.skill_dir else None
    user_skill_dir_path = (
        agent.config.tools.user_skill_dir.resolve()
        if agent.config.tools.user_skill_dir else None
    )
    workspace_skill_dir_path = (
        (agent.config.agent.workspace_dir / "skills").resolve()
        if agent.config.agent.workspace_dir else None
    )
    for item in items:
        raw = skills_raw.get(item.get("name", "")) or {}
        path_str = str(raw.get("path", "") or "")
        scope = "unknown"
        if item.get("builtin"):
            scope = "builtin"
        elif path_str:
            p = Path(path_str).resolve()
            if skill_dir_path and str(p).startswith(str(skill_dir_path)):
                scope = "system"
            elif user_skill_dir_path and str(p).startswith(str(user_skill_dir_path)):
                scope = "user"
            elif workspace_skill_dir_path and str(p).startswith(str(workspace_skill_dir_path)):
                scope = "workspace"
        item["scope"] = scope
        item["scope_label"] = {
            "builtin": "Built-in",
            "system": "System",
            "user": "User",
            "workspace": "Workspace",
        }.get(scope, "Unknown")

    skill_dir = str(agent.config.tools.skill_dir or "")
    user_skill_dir = str(agent.config.tools.user_skill_dir or "")
    await ws.send(json.dumps({
        "type": "skills",
        "items": items,
        "skill_dir": skill_dir,
        "user_skill_dir": user_skill_dir,
        "configured": bool(skill_dir or user_skill_dir),
    }))


async def handle_save_skill(ws, data: dict, gateway) -> None:
    name = str(data.get("name") or "").strip()
    content = str(data.get("content") or "").strip()
    description = str(data.get("description") or "").strip()
    if not name or not content:
        await ws.send(json.dumps({
            "type": "skill_saved",
            "ok": False,
            "error": "name and content are required",
        }))
        return
    agent = gateway.base_agent
    skill_dir = agent.config.tools.user_skill_dir or agent.config.tools.skill_dir
    if not skill_dir:
        await ws.send(json.dumps({
            "type": "skill_saved",
            "ok": False,
            "error": "No skill directory configured. Set tools.user_skill_dir in hushclaw.toml.",
        }))
        return
    try:
        from hushclaw.skills.writer import write_skill
        path = write_skill(name=name, content=content, description=description, skill_dir=skill_dir)
        registry = getattr(agent, "_skill_registry", None)
        if registry is not None:
            registry.reload()
        await ws.send(json.dumps({
            "type": "skill_saved",
            "ok": True,
            "name": name,
            "path": str(path),
        }))
        # Push updated skills list so panel refreshes immediately
        await handle_list_skills(ws, gateway)
    except Exception as exc:
        log.error("save_skill error: %s", exc, exc_info=True)
        await ws.send(json.dumps({
            "type": "skill_saved",
            "ok": False,
            "error": str(exc),
        }))


async def handle_delete_skill(ws, data: dict, gateway) -> None:
    name = str(data.get("name") or "").strip()
    if not name:
        await ws.send(json.dumps({"type": "skill_deleted", "name": "", "ok": False, "error": "Missing skill name"}))
        return
    agent = gateway.base_agent
    registry = getattr(agent, "_skill_registry", None)
    if registry is None:
        await ws.send(json.dumps({"type": "skill_deleted", "name": name, "ok": False, "error": "No skill registry"}))
        return
    ok, error = registry.delete_skill(name)
    await ws.send(json.dumps({"type": "skill_deleted", "name": name, "ok": ok, "error": error}))
    if ok:
        await handle_list_skills(ws, gateway)


async def handle_install_skill_repo(ws, data: dict, gateway) -> None:
    import re

    url = data.get("url", "").strip()

    # Reject unsafe URLs: must be https://, no whitespace or shell metacharacters
    if not url.startswith("https://") or re.search(r'[\s$;|&<>`\'"\\]', url):
        await ws.send(json.dumps({
            "type": "skill_install_result",
            "ok": False,
            "url": url,
            "error": "Invalid URL. Only plain HTTPS git URLs are supported.",
        }))
        return

    agent = gateway.base_agent
    install_skill_dir = agent.config.tools.user_skill_dir or agent.config.tools.skill_dir
    if not install_skill_dir:
        await ws.send(json.dumps({
            "type": "skill_install_result",
            "ok": False,
            "url": url,
            "error": (
                "skill_dir is not configured. Set [tools] skill_dir or user_skill_dir "
                "in hushclaw.toml, then retry."
            ),
        }))
        return

    repo_name = url.rstrip("/").rstrip(".git").rsplit("/", 1)[-1]
    target_dir = install_skill_dir / repo_name

    try:
        install_skill_dir.mkdir(parents=True, exist_ok=True)

        if target_dir.exists():
            await ws.send(json.dumps({
                "type": "skill_install_progress",
                "url": url,
                "message": f"Updating {repo_name}…",
            }))
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(target_dir), "pull",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            await ws.send(json.dumps({
                "type": "skill_install_progress",
                "url": url,
                "message": f"Cloning {repo_name}…",
            }))
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth=1", url, str(target_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": False,
                "url": url,
                "error": "Git operation timed out after 120 seconds.",
            }))
            return

        if proc.returncode != 0:
            lines = stderr.decode(errors="ignore").strip().splitlines()
            err = lines[-1] if lines else "Unknown git error"
            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": False,
                "url": url,
                "error": err,
            }))
            return

        result = await post_install(
            ws, target_dir, repo_name, url, "git", agent, install_skill_dir
        )
        result["url"] = url
        result["repo"] = repo_name
        await ws.send(json.dumps(result))

    except Exception as exc:
        log.error("install_skill_repo error: %s", exc, exc_info=True)
        await ws.send(json.dumps({
            "type": "skill_install_result",
            "ok": False,
            "url": url,
            "error": str(exc),
        }))


async def handle_install_skill_zip(ws, data: dict, gateway) -> None:
    import io
    import re
    import urllib.request
    import zipfile
    from hushclaw.util.ssl_context import make_ssl_context

    url  = data.get("url", "").strip()
    slug = data.get("slug", "").strip()

    if not url.startswith("https://") or re.search(r'[\s$;|&<>`\'"\\]', url):
        await ws.send(json.dumps({
            "type": "skill_install_result",
            "ok": False,
            "url": url,
            "error": "Invalid URL. Only plain HTTPS zip URLs are supported.",
        }))
        return

    if not slug or re.search(r'[^a-zA-Z0-9_\-]', slug):
        await ws.send(json.dumps({
            "type": "skill_install_result",
            "ok": False,
            "url": url,
            "error": "Invalid slug. Use only letters, numbers, hyphens, and underscores.",
        }))
        return

    agent = gateway.base_agent
    install_skill_dir = agent.config.tools.user_skill_dir or agent.config.tools.skill_dir
    if not install_skill_dir:
        await ws.send(json.dumps({
            "type": "skill_install_result",
            "ok": False,
            "url": url,
            "error": (
                "skill_dir is not configured. Set [tools] skill_dir or user_skill_dir "
                "in hushclaw.toml, then retry."
            ),
        }))
        return

    target_dir = install_skill_dir / slug

    try:
        install_skill_dir.mkdir(parents=True, exist_ok=True)

        await ws.send(json.dumps({
            "type": "skill_install_progress",
            "url": url,
            "message": f"Downloading {slug}…",
        }))

        loop = asyncio.get_event_loop()
        req = urllib.request.Request(url, headers={"User-Agent": "HushClaw/1.0"})

        def _download():
            with urllib.request.urlopen(req, timeout=60, context=make_ssl_context()) as resp:
                return resp.read()

        try:
            raw_bytes = await asyncio.wait_for(
                loop.run_in_executor(None, _download), timeout=65
            )
        except asyncio.TimeoutError:
            await ws.send(json.dumps({
                "type": "skill_install_result",
                "ok": False,
                "url": url,
                "error": "Download timed out after 60 seconds.",
            }))
            return

        await ws.send(json.dumps({
            "type": "skill_install_progress",
            "url": url,
            "message": f"Extracting {slug}…",
        }))

        buf = io.BytesIO(raw_bytes)
        target_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            prefix = names[0].split("/")[0] + "/" if names else ""
            strip = (
                bool(prefix)
                and len(prefix) > 1
                and all(n.startswith(prefix) for n in names)
            )
            for member in zf.infolist():
                rel = member.filename[len(prefix):] if strip else member.filename
                if not rel:
                    continue
                dest = target_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not member.is_dir():
                    dest.write_bytes(zf.read(member.filename))

        result = await post_install(
            ws, target_dir, slug, url, "zip", agent, install_skill_dir
        )
        result["url"] = url
        await ws.send(json.dumps(result))

    except Exception as exc:
        log.error("install_skill_zip error: %s", exc, exc_info=True)
        await ws.send(json.dumps({
            "type": "skill_install_result",
            "ok": False,
            "url": url,
            "error": str(exc),
        }))


# ---------------------------------------------------------------------------
# Export skills → ZIP download
# ---------------------------------------------------------------------------

_EXPORT_INCLUDE = {"SKILL.md", "requirements.txt", "README.md"}
_EXPORT_SKIP_DIRS = {"__pycache__", ".git", "staging", "clawhub"}


async def handle_export_skills(ws, data: dict, gateway) -> None:
    """Pack selected (or all non-builtin) user skills into a ZIP and return
    it as a base64-encoded payload for the browser to download."""
    import base64
    import io
    import zipfile
    from datetime import datetime

    agent   = gateway.base_agent
    registry = getattr(agent, "_skill_registry", None)
    if not registry:
        await ws.send(json.dumps({
            "type": "skill_export_ready",
            "ok": False,
            "error": "Skill registry not available.",
        }))
        return

    requested: list[str] = data.get("names") or []  # [] = all non-builtins

    skills_to_export = [
        s for s in registry.list_all()
        if s.get("tier") != "builtin"
        and (not requested or s["name"] in requested)
    ]

    if not skills_to_export:
        await ws.send(json.dumps({
            "type": "skill_export_ready",
            "ok": False,
            "error": "No exportable (non-builtin) skills found.",
        }))
        return

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for skill in skills_to_export:
            skill_dir = Path(skill["path"]).parent
            slug      = skill_dir.name

            # Add top-level files (SKILL.md, README.md, requirements.txt)
            for fname in _EXPORT_INCLUDE:
                fpath = skill_dir / fname
                if fpath.is_file():
                    zf.write(fpath, f"{slug}/{fname}")

            # Add tools/ directory if present
            tools_dir = skill_dir / "tools"
            if tools_dir.is_dir():
                for py_file in sorted(tools_dir.glob("*.py")):
                    zf.write(py_file, f"{slug}/tools/{py_file.name}")

    zip_bytes = buf.getvalue()
    filename  = f"hushclaw-skills-{datetime.now().strftime('%Y-%m-%d')}.zip"
    await ws.send(json.dumps({
        "type":     "skill_export_ready",
        "ok":       True,
        "filename": filename,
        "data":     base64.b64encode(zip_bytes).decode(),
        "count":    len(skills_to_export),
    }))


# ---------------------------------------------------------------------------
# Import skills from a locally uploaded ZIP
# ---------------------------------------------------------------------------

_IMPORT_MAX_BYTES = 20 * 1024 * 1024  # 20 MB safety cap


async def handle_import_skill_zip(ws, data: dict, gateway) -> None:
    """Receive a base64-encoded ZIP file, extract it, and install each skill
    directory found inside it using the existing post_install() pipeline."""
    import base64
    import io
    import shutil
    import tempfile
    import zipfile

    b64_data: str = data.get("data", "")
    filename: str = data.get("filename", "skills.zip")

    # --- decode & validate ---------------------------------------------------
    try:
        raw_bytes = base64.b64decode(b64_data)
    except Exception:
        await ws.send(json.dumps({
            "type": "skill_import_result",
            "ok": False,
            "error": "Invalid base64 payload.",
        }))
        return

    if len(raw_bytes) > _IMPORT_MAX_BYTES:
        await ws.send(json.dumps({
            "type": "skill_import_result",
            "ok": False,
            "error": f"ZIP too large (max {_IMPORT_MAX_BYTES // 1024 // 1024} MB).",
        }))
        return

    if not zipfile.is_zipfile(io.BytesIO(raw_bytes)):
        await ws.send(json.dumps({
            "type": "skill_import_result",
            "ok": False,
            "error": "File is not a valid ZIP archive.",
        }))
        return

    # --- resolve install directory -------------------------------------------
    agent             = gateway.base_agent
    install_skill_dir = agent.config.tools.user_skill_dir or agent.config.tools.skill_dir
    if not install_skill_dir:
        await ws.send(json.dumps({
            "type": "skill_import_result",
            "ok": False,
            "error": (
                "skill_dir is not configured. Set [tools] skill_dir or user_skill_dir "
                "in hushclaw.toml, then retry."
            ),
        }))
        return

    install_skill_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="hc_skill_import_"))
    installed: list[str] = []
    errors:    list[dict] = []

    try:
        # --- extract to temp dir ---------------------------------------------
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            for member in zf.infolist():
                # Path traversal guard
                rel = Path(member.filename)
                if rel.is_absolute() or ".." in rel.parts:
                    continue
                dest = tmp_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not member.is_dir():
                    dest.write_bytes(zf.read(member.filename))

        # --- find skills (SKILL.md at depth 1 or 2) -------------------------
        skill_dirs_found: list[Path] = []
        for skill_md in tmp_dir.rglob("SKILL.md"):
            depth = len(skill_md.relative_to(tmp_dir).parts)
            if depth <= 2:  # tmp/SKILL.md (depth=1) or tmp/slug/SKILL.md (depth=2)
                skill_dirs_found.append(skill_md.parent)

        if not skill_dirs_found:
            await ws.send(json.dumps({
                "type": "skill_import_result",
                "ok": False,
                "error": "No SKILL.md found in ZIP. Not a valid HushClaw skill pack.",
            }))
            return

        await ws.send(json.dumps({
            "type": "skill_install_progress",
            "slug": filename,
            "message": f"Found {len(skill_dirs_found)} skill(s) — installing…",
        }))

        # --- install each skill ----------------------------------------------
        from hushclaw.skills.writer import _slugify  # type: ignore[attr-defined]

        for src_dir in skill_dirs_found:
            # Derive slug from SKILL.md name field, fall back to dir name
            skill_md_path = src_dir / "SKILL.md"
            slug = src_dir.name
            try:
                text = skill_md_path.read_text(encoding="utf-8", errors="replace")
                # Quick parse of 'name:' from frontmatter
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("name:"):
                        raw_name = stripped[5:].strip().strip('"').strip("'")
                        if raw_name:
                            slug = _slugify(raw_name)
                        break
            except Exception:
                pass

            target_dir = install_skill_dir / slug
            try:
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                shutil.copytree(src_dir, target_dir)

                result = await post_install(
                    ws, target_dir, slug, f"upload:{filename}", "zip",
                    agent, install_skill_dir,
                )
                if result.get("ok"):
                    installed.append(slug)
                else:
                    errors.append({"slug": slug, "error": result.get("error", "unknown")})
            except Exception as exc:
                log.error("import_skill_zip: failed to install %s: %s", slug, exc)
                errors.append({"slug": slug, "error": str(exc)})

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    await ws.send(json.dumps({
        "type":      "skill_import_result",
        "ok":        bool(installed),
        "installed": installed,
        "errors":    errors,
    }))

