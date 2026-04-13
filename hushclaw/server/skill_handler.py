"""Skill management handler functions — extracted from server.py.

Handles list_skills, save_skill, delete_skill, install_skill_repo,
install_skill_zip, export_skills, and import_skill_zip WebSocket messages.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger("hushclaw.server.skills")

# ---------------------------------------------------------------------------
# Handler functions
# ---------------------------------------------------------------------------

async def handle_list_skills(ws, gateway) -> None:
    from hushclaw.skills.installer import read_lock
    agent = gateway.base_agent
    registry = getattr(agent, "_skill_registry", None)
    # Always reload from disk so new/deleted skills are reflected immediately
    # without needing a server restart (e.g. after the agent manually extracts a ZIP).
    if registry is not None:
        registry.reload()
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
    from hushclaw.skills.installer import SkillInstaller

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

    install_skill_dir.mkdir(parents=True, exist_ok=True)
    repo_name = url.rstrip("/").rstrip(".git").rsplit("/", 1)[-1]

    async def _prog(msg: str) -> None:
        await ws.send(json.dumps({
            "type": "skill_install_progress",
            "url": url,
            "message": msg,
        }))

    try:
        installer = SkillInstaller()
        result = await installer.install(
            source=url,
            install_dir=install_skill_dir,
            skill_registry=getattr(agent, "_skill_registry", None),
            tool_registry=agent.registry,
            gateway=gateway,
            on_progress=_prog,
        )
        await ws.send(json.dumps(result.to_ws_result(url=url, repo=repo_name)))
    except Exception as exc:
        log.error("install_skill_repo error: %s", exc, exc_info=True)
        await ws.send(json.dumps({
            "type": "skill_install_result",
            "ok": False,
            "url": url,
            "error": str(exc),
        }))


async def handle_install_skill_zip(ws, data: dict, gateway) -> None:
    import re
    from hushclaw.skills.installer import SkillInstaller

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

    install_skill_dir.mkdir(parents=True, exist_ok=True)

    async def _prog(msg: str) -> None:
        await ws.send(json.dumps({
            "type": "skill_install_progress",
            "url": url,
            "message": msg,
        }))

    try:
        installer = SkillInstaller()
        result = await installer.install(
            source=url,
            install_dir=install_skill_dir,
            slug=slug,
            skill_registry=getattr(agent, "_skill_registry", None),
            tool_registry=agent.registry,
            gateway=gateway,
            on_progress=_prog,
        )
        await ws.send(json.dumps(result.to_ws_result(url=url)))
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
    it as a base64-encoded payload for the browser to download.

    data["names"] = []        → export ALL non-builtin skills
    data["names"] = ["slug"]  → export exactly that skill
    """
    import base64
    import io
    import zipfile
    from datetime import datetime

    agent    = gateway.base_agent
    registry = getattr(agent, "_skill_registry", None)
    if not registry:
        await ws.send(json.dumps({
            "type": "skill_export_ready",
            "ok": False,
            "error": "Skill registry not available.",
        }))
        return

    requested: list[str] = data.get("names") or []  # [] = all non-builtins

    # Use _skills directly — list_all() strips "path" and "tier"
    skills_raw = getattr(registry, "_skills", {})
    skills_to_export = [
        s for s in skills_raw.values()
        if s.get("tier") != "builtin"
        and s.get("path")
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
    date_str  = datetime.now().strftime("%Y-%m-%d")
    if len(skills_to_export) == 1:
        slug_name = Path(skills_to_export[0]["path"]).parent.name
        filename  = f"hushclaw-skill-{slug_name}-{date_str}.zip"
    else:
        filename  = f"hushclaw-skills-{date_str}.zip"

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
        from hushclaw.skills.installer import SkillInstaller

        installer = SkillInstaller()

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
                            slug = SkillInstaller.slugify(raw_name)
                        break
            except Exception:
                pass

            target_dir = install_skill_dir / slug
            try:
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                shutil.copytree(src_dir, target_dir)

                async def _prog(msg: str, _slug: str = slug) -> None:
                    await ws.send(json.dumps({
                        "type": "skill_install_progress",
                        "slug": _slug,
                        "message": msg,
                    }))

                result = await installer.post_install(
                    target_dir=target_dir,
                    slug=slug,
                    source=f"upload:{filename}",
                    source_type="zip",
                    install_dir=install_skill_dir,
                    skill_registry=getattr(agent, "_skill_registry", None),
                    tool_registry=agent.registry,
                    gateway=gateway,
                    on_progress=_prog,
                )
                if result.ok:
                    installed.append(slug)
                else:
                    errors.append({"slug": slug, "error": result.error})
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

