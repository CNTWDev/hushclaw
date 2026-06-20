"""Skill management handler functions — extracted from server.py.

Handles skill library and external skill source WebSocket messages.
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


async def _prepare_user_skill_dir(ws, agent, *, result_type: str, url: str = "") -> Path | None:
    install_skill_dir = agent.config.tools.user_skill_dir
    try:
        install_skill_dir.mkdir(parents=True, exist_ok=True)
        return install_skill_dir
    except Exception as exc:
        log.error("prepare user skill dir error: %s", exc, exc_info=True)
        payload = {"type": result_type, "ok": False, "error": str(exc)}
        if url:
            payload["url"] = url
        await ws.send(json.dumps(payload))
        return None


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


def _tool_names_for_agent(gateway, agent_name: str) -> set[str]:
    try:
        pool = gateway.get_pool(agent_name)
        registry = getattr(getattr(pool, "_agent", None), "registry", None)
        if registry and hasattr(registry, "list_tools"):
            return {str(getattr(td, "name", "")) for td in registry.list_tools() if getattr(td, "name", "")}
    except Exception:
        log.debug("failed to resolve tools for agent=%s", agent_name, exc_info=True)
    return set()


def build_agent_runtime_status(gateway, agent_name: str) -> dict:
    """Return runtime capability status for a specific agent without enumerating skills."""
    name = str(agent_name or "default").strip() or "default"
    defn = gateway.get_agent_def(name) if hasattr(gateway, "get_agent_def") else None
    if not defn:
        return {
            "type": "agent_runtime_status",
            "ok": False,
            "agent": name,
            "error": f"Agent '{name}' not found",
        }

    tool_names = _tool_names_for_agent(gateway, name)
    skill_loader_tools = sorted({"use_skill", "skill_view"} & tool_names)
    can_load_skills = bool(skill_loader_tools)
    skill_discovery_tools = sorted({"search_skills", "list_skills"} & tool_names)
    can_discover_skills = bool(skill_discovery_tools)
    skill_install_tools = sorted({"install_skill"} & tool_names)
    can_install_skills = bool(skill_install_tools)
    skill_inspect_tools = sorted({"inspect_skill_source"} & tool_names)
    can_inspect_skill_sources = bool(skill_inspect_tools)
    custom_tools = list(defn.get("tools") or [])
    inherits_global_tools = name == "default" or not custom_tools
    warnings: list[str] = []
    if custom_tools and not can_load_skills:
        warnings.append("Custom tools do not include use_skill or skill_view, so this agent cannot load prompt skills at runtime.")
    if can_load_skills and not can_discover_skills:
        warnings.append("Skill loading is enabled, but search_skills or list_skills is not available for discovery.")
    if can_install_skills and not can_inspect_skill_sources:
        warnings.append("Skill installation is enabled, but inspect_skill_source is unavailable. This agent may install external skills without a structured preview.")

    return {
        "type": "agent_runtime_status",
        "ok": True,
        "agent": name,
        "inherits_global_tools": inherits_global_tools,
        "custom_tools": custom_tools,
        "effective_tool_count": len(tool_names),
        "can_load_skills": can_load_skills,
        "can_discover_skills": can_discover_skills,
        "can_install_skills": can_install_skills,
        "can_inspect_skill_sources": can_inspect_skill_sources,
        "skill_loader_tools": skill_loader_tools,
        "skill_discovery_tools": skill_discovery_tools,
        "skill_install_tools": skill_install_tools,
        "skill_inspect_tools": skill_inspect_tools,
        "warnings": warnings,
        "tools": sorted(tool_names),
    }


async def handle_get_agent_runtime_status(ws, data: dict, gateway) -> None:
    payload = build_agent_runtime_status(gateway, str(data.get("name") or "default"))
    await ws.send(json.dumps(payload))


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
    if registry and hasattr(registry, "list_all"):
        catalog = registry.list_all()
    elif registry and hasattr(registry, "query"):
        catalog_result = registry.query(
            q="",
            scope="all",
            status="all",
            sort="name",
            offset=0,
            limit=300,
        )
        catalog = catalog_result.get("items", [])
    elif registry:
        catalog = registry.list_all() if hasattr(registry, "list_all") else []
    else:
        catalog = []

    # Merge installed_version from lockfile(s)
    lock: dict = {}
    for skill_dir_path in [agent.config.tools.skill_dir, agent.config.tools.user_skill_dir]:
        if skill_dir_path and skill_dir_path.exists():
            lock.update(read_lock(skill_dir_path))
    if lock:
        for item in [*items, *catalog]:
            entry = lock.get(item["name"])
            if entry:
                item["installed_version"] = entry.get("version", "")
                item["installed_at"] = entry.get("installed_at", 0)

    _decorate_skill_items(agent, items, skills_raw)
    _decorate_skill_items(agent, catalog, skills_raw)

    payload = {
        "type": "skills",
        "items": items,
        "catalog": catalog,
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


def _target_skill_dir_for_scope(agent, scope: str) -> Path | None:
    wanted = str(scope or "user").strip().lower()
    if wanted == "workspace":
        workspace_dir = getattr(getattr(agent.config, "agent", None), "workspace_dir", None)
        return (workspace_dir / "skills") if workspace_dir else None
    return agent.config.tools.user_skill_dir


async def handle_inspect_skill_source(ws, data: dict, gateway) -> None:
    source = str(data.get("source") or data.get("url") or "").strip()
    ref = str(data.get("ref") or "").strip()
    subpath = str(data.get("subpath") or data.get("selected_candidate_path") or "").strip()
    if not source:
        await ws.send(json.dumps({
            "type": "skill_source_inspected",
            "ok": False,
            "source": "",
            "error": "Missing source",
        }))
        return
    agent = gateway.base_agent
    skill_manager = getattr(agent, "_skill_manager", None)
    if skill_manager is None:
        await ws.send(json.dumps({
            "type": "skill_source_inspected",
            "ok": False,
            "source": source,
            "error": "Skill manager not available",
        }))
        return
    try:
        result = await skill_manager.inspect_source(source, ref=ref, subpath=subpath)
        await ws.send(json.dumps({
            "type": "skill_source_inspected",
            **result,
        }))
    except Exception as exc:
        log.error("inspect_skill_source error: %s", exc, exc_info=True)
        await ws.send(json.dumps({
            "type": "skill_source_inspected",
            "ok": False,
            "source": source,
            "error": str(exc),
        }))


async def handle_install_skill_source(ws, data: dict, gateway) -> None:
    source = str(data.get("source") or data.get("url") or "").strip()
    ref = str(data.get("ref") or "").strip()
    subpath = str(data.get("subpath") or data.get("selected_candidate_path") or "").strip()
    slug = str(data.get("slug") or "").strip()
    scope = str(data.get("scope") or "user").strip().lower() or "user"
    if not source:
        await ws.send(json.dumps({
            "type": "skill_install_result",
            "ok": False,
            "source": "",
            "url": "",
            "error": "Missing source",
        }))
        return
    agent = gateway.base_agent
    install_skill_dir = _target_skill_dir_for_scope(agent, scope)
    if install_skill_dir is None:
        await ws.send(json.dumps({
            "type": "skill_install_result",
            "ok": False,
            "source": source,
            "url": source,
            "error": "Selected install scope is not available.",
        }))
        return
    try:
        install_skill_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        await ws.send(json.dumps({
            "type": "skill_install_result",
            "ok": False,
            "source": source,
            "url": source,
            "error": str(exc),
        }))
        return

    async def _prog(msg: str) -> None:
        await ws.send(json.dumps({
            "type": "skill_install_progress",
            "source": source,
            "url": source,
            "message": msg,
        }))

    try:
        skill_manager = getattr(agent, "_skill_manager", None)
        if skill_manager is None:
            raise RuntimeError("Skill manager not available")
        result = await skill_manager.install(
            source,
            slug=slug or None,
            tier="workspace" if scope == "workspace" else "user",
            ref=ref,
            subpath=subpath,
            on_progress=_prog,
        )
        payload = result.to_ws_result(url=source)
        payload["source"] = source
        payload["scope"] = scope
        payload["ref"] = ref
        payload["subpath"] = subpath
        await ws.send(json.dumps(payload))
    except Exception as exc:
        log.error("install_skill_source error: %s", exc, exc_info=True)
        await ws.send(json.dumps({
            "type": "skill_install_result",
            "ok": False,
            "source": source,
            "url": source,
            "scope": scope,
            "error": str(exc),
        }))


async def handle_install_skill_repo(ws, data: dict, gateway) -> None:
    payload = {
        "source": str(data.get("url") or "").strip(),
        "scope": str(data.get("scope") or "user"),
        "ref": str(data.get("ref") or "").strip(),
        "subpath": str(data.get("subpath") or "").strip(),
        "slug": str(data.get("slug") or "").strip(),
    }
    await handle_install_skill_source(ws, payload, gateway)


async def handle_install_skill_zip(ws, data: dict, gateway) -> None:
    payload = {
        "source": str(data.get("url") or "").strip(),
        "scope": str(data.get("scope") or "user"),
        "slug": str(data.get("slug") or "").strip(),
    }
    await handle_install_skill_source(ws, payload, gateway)


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
    install_skill_dir = await _prepare_user_skill_dir(ws, agent, result_type="skill_import_result")
    if install_skill_dir is None:
        return
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
