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


_SKILL_PACKAGE_SKIP_DIRS = {"__pycache__", ".git", "staging", "clawhub", "node_modules"}
_SKILL_PACKAGE_SKIP_FILES = {".DS_Store"}
_SKILL_PACKAGE_SKIP_SUFFIXES = {".pyc", ".pyo"}


def _iter_skill_package_files(skill_dir: Path):
    """Yield exportable files inside *skill_dir* while skipping runtime junk."""
    for path in sorted(skill_dir.rglob("*")):
        rel = path.relative_to(skill_dir)
        if any(part in _SKILL_PACKAGE_SKIP_DIRS for part in rel.parts):
            continue
        if not path.is_file():
            continue
        if path.name in _SKILL_PACKAGE_SKIP_FILES:
            continue
        if path.suffix.lower() in _SKILL_PACKAGE_SKIP_SUFFIXES:
            continue
        yield path, rel


def _find_importable_skill_dirs(root: Path) -> list[Path]:
    """Return top-level skill directories (those containing SKILL.md) inside *root*.

    Nested SKILL.md files (e.g. examples/SKILL.md inside a skill package) are
    excluded — only the outermost SKILL.md directory in each branch is returned.
    Supports wrapper folders like release-name/skill-name/SKILL.md.
    """
    candidates: list[Path] = []
    for skill_md in sorted(root.rglob("SKILL.md")):
        rel = skill_md.relative_to(root)
        if any(part in _SKILL_PACKAGE_SKIP_DIRS for part in rel.parts):
            continue
        candidates.append(skill_md.parent)

    if not candidates:
        return []

    # Exclude any directory that is a subdirectory of another candidate.
    # This prevents examples/SKILL.md or tests/SKILL.md from being treated as
    # top-level skills when they live inside a skill package.
    resolved = [c.resolve() for c in candidates]
    result: list[Path] = []
    for i, d in enumerate(resolved):
        if not any(j != i and str(d).startswith(str(other) + "/") for j, other in enumerate(resolved)):
            result.append(candidates[i])
    return result


# ---------------------------------------------------------------------------
# Handler functions
# ---------------------------------------------------------------------------

def _skill_scope_paths(agent) -> tuple[Path | None, Path | None, Path | None]:
    skill_dir_path = agent.config.tools.skill_dir.resolve() if agent.config.tools.skill_dir else None
    user_skill_dir_path = (
        agent.config.tools.user_skill_dir.resolve()
        if agent.config.tools.user_skill_dir else None
    )
    workspace_skill_dir_path = (
        (agent.config.agent.workspace_dir / "skills").resolve()
        if getattr(agent.config.agent, "workspace_dir", None) else None
    )
    return skill_dir_path, user_skill_dir_path, workspace_skill_dir_path


def _decorate_skill_items(agent, items: list[dict], raw_by_name: dict | None = None) -> list[dict]:
    raw_by_name = raw_by_name or {}
    skill_dir_path, user_skill_dir_path, workspace_skill_dir_path = _skill_scope_paths(agent)
    for item in items:
        raw = raw_by_name.get(item.get("name", "")) or {}
        path_str = str(raw.get("path", "") or item.get("path", "") or "")
        scope = str(item.get("scope") or item.get("tier") or "unknown")
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
    return items


def _skill_dirs_payload(agent) -> dict:
    skill_dir = str(agent.config.tools.skill_dir or "")
    user_skill_dir = str(agent.config.tools.user_skill_dir or "")
    return {
        "skill_dir": skill_dir,
        "user_skill_dir": user_skill_dir,
        "configured": bool(skill_dir or user_skill_dir),
    }


async def handle_list_skills(ws, gateway, data: dict | None = None) -> None:
    from hushclaw.skills.installer import read_lock
    data = data or {}
    agent = gateway.base_agent
    registry = getattr(agent, "_skill_registry", None)
    # Always reload from disk so new/deleted skills are reflected immediately
    # without needing a server restart (e.g. after the agent manually extracts a ZIP).
    if registry is not None:
        registry.reload()
    if registry and hasattr(registry, "query"):
        result = registry.query(
            q=str(data.get("q") or ""),
            scope=str(data.get("scope") or "all"),
            status=str(data.get("status") or "all"),
            sort=str(data.get("sort") or "name"),
            offset=int(data.get("offset") or 0),
            limit=int(data.get("limit") or 80),
        )
        items = result.get("items", [])
    elif registry:
        items = registry.list_all() if hasattr(registry, "list_all") else []
        result = {"items": items, "total": len(items), "offset": 0, "limit": len(items) or 80, "counts": {}}
    else:
        result = {"items": [], "total": 0, "offset": 0, "limit": 80, "counts": {}}
        items = []
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

    _decorate_skill_items(agent, items, skills_raw)

    payload = {
        "type": "skills",
        "items": items,
        "total": result.get("total", len(items)),
        "offset": result.get("offset", 0),
        "limit": result.get("limit", 80),
        "counts": result.get("counts", {}),
        "filters": {
            "q": str(data.get("q") or ""),
            "scope": str(data.get("scope") or "all"),
            "status": str(data.get("status") or "all"),
            "sort": str(data.get("sort") or "name"),
        },
    }
    payload.update(_skill_dirs_payload(agent))
    await ws.send(json.dumps(payload))


async def handle_get_skill_detail(ws, data: dict, gateway) -> None:
    name = str(data.get("name") or "").strip()
    agent = gateway.base_agent
    registry = getattr(agent, "_skill_registry", None)
    if not registry or not name:
        await ws.send(json.dumps({"type": "skill_detail", "ok": False, "name": name, "error": "Skill not found"}))
        return
    detail = registry.detail(name)
    if not detail:
        await ws.send(json.dumps({"type": "skill_detail", "ok": False, "name": name, "error": "Skill not found"}))
        return
    _decorate_skill_items(agent, [detail], getattr(registry, "_skills", {}))
    await ws.send(json.dumps({"type": "skill_detail", "ok": True, "item": detail}))


async def handle_check_skills_health(ws, gateway) -> None:
    agent = gateway.base_agent
    registry = getattr(agent, "_skill_registry", None)
    if not registry:
        await ws.send(json.dumps({"type": "skills_health", "ok": False, "error": "Skill registry not available"}))
        return
    report = registry.health()
    _decorate_skill_items(agent, report.get("items", []), getattr(registry, "_skills", {}))
    report["type"] = "skills_health"
    await ws.send(json.dumps(report))


async def handle_set_skill_enabled(ws, data: dict, gateway) -> None:
    name = str(data.get("name") or "").strip()
    enabled = bool(data.get("enabled"))
    agent = gateway.base_agent
    registry = getattr(agent, "_skill_registry", None)
    if not registry or not name:
        await ws.send(json.dumps({"type": "skill_enabled", "ok": False, "name": name, "enabled": enabled, "error": "Skill not found"}))
        return
    ok, error, item = registry.set_enabled(name, enabled)
    if item:
        _decorate_skill_items(agent, [item], getattr(registry, "_skills", {}))
    await ws.send(json.dumps({
        "type": "skill_enabled",
        "ok": ok,
        "name": name,
        "enabled": enabled,
        "item": item,
        "error": error,
    }))
    if ok:
        await handle_list_skills(ws, gateway)


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
    try:
        skill_manager = getattr(agent, "_skill_manager", None)
        if skill_manager is None:
            raise RuntimeError("Skill manager not available")
        path = skill_manager.create(name=name, content=content, description=description)
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
    install_skill_dir = agent.config.tools.user_skill_dir
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
    install_skill_dir = agent.config.tools.user_skill_dir
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

    requested: list[str] = data.get("names") or []  # [] = all user-installed skills

    # Use _skills directly — list_all() strips "path" and "tier"
    skills_raw = getattr(registry, "_skills", {})
    skills_to_export = [
        s for s in skills_raw.values()
        if s.get("tier") == "user"
        and s.get("path")
        and (not requested or s["name"] in requested)
    ]

    if not skills_to_export:
        await ws.send(json.dumps({
            "type": "skill_export_ready",
            "ok": False,
            "error": "No exportable user skills found.",
        }))
        return

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for skill in skills_to_export:
            skill_dir = Path(skill["path"]).parent
            slug      = skill_dir.name

            # Export the whole skill package, not just SKILL.md/tools.py.
            # Many skills depend on references/, assets/, scripts/, or data files.
            for fpath, rel in _iter_skill_package_files(skill_dir):
                zf.write(fpath, str(Path(slug) / rel))

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
    install_skill_dir = agent.config.tools.user_skill_dir
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

        # --- find skills anywhere in the extracted archive -------------------
        # Accept wrapper folders like release-name/skill-name/SKILL.md and
        # export round-trips containing nested support files.
        skill_dirs_found = _find_importable_skill_dirs(tmp_dir)

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
