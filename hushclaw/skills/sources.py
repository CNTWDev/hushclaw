"""Normalized skill source parsing and inspection.

This module provides a single source-of-truth for:
- parsing raw skill sources (git / GitHub tree / zip / local)
- inspecting candidate SKILL.md directories before installation
- reading optional Claude-style plugin metadata to locate skill folders
"""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from hushclaw.skills.validator import SkillValidator

_SKILL_SKIP_DIRS = {
    "__pycache__", ".git", ".hg", ".svn", "node_modules", ".venv", "venv",
}
_PLUGIN_MANIFEST_DIRS = (".claude-plugin", ".codex-plugin")


@dataclass(slots=True)
class SkillSourceSpec:
    original_source: str
    provider: str
    source_type: str
    source: str
    repo_url: str = ""
    ref: str = ""
    subpath: str = ""


async def _stream_lines(
    stream: asyncio.StreamReader,
    *,
    deadline: float,
) -> list[str]:
    collected: list[str] = []
    loop = asyncio.get_event_loop()
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        raw = await asyncio.wait_for(stream.readline(), timeout=remaining)
        if not raw:
            break
        line = raw.decode(errors="ignore").rstrip("\r\n").strip()
        if line:
            collected.append(line)
    return collected


def _is_http_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _detect_source_type(source: str) -> str:
    s = source.strip()
    if _is_http_url(s):
        if s.lower().endswith(".zip"):
            return "https_zip"
        parsed = urllib.parse.urlparse(s)
        if parsed.netloc == "github.com" and "/tree/" in parsed.path:
            return "github_tree"
        if s.rstrip("/").endswith(".git") or any(
            host in parsed.netloc for host in ("github.com", "gitlab.com", "bitbucket.org")
        ):
            return "git"
        return "https_zip"
    p = Path(s).expanduser()
    if p.suffix.lower() == ".zip":
        return "local_zip"
    return "local_dir"


async def _git_ls_remote_refs(repo_url: str) -> list[str]:
    proc = await asyncio.create_subprocess_exec(
        "git", "ls-remote", "--heads", "--tags", repo_url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        deadline = asyncio.get_event_loop().time() + 20
        out_lines = await _stream_lines(proc.stdout, deadline=deadline)
        await _stream_lines(proc.stderr, deadline=deadline)
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        proc.kill()
        return []
    if proc.returncode != 0:
        return []
    refs: list[str] = []
    for line in out_lines:
        parts = line.split()
        if len(parts) != 2:
            continue
        ref_name = parts[1]
        if ref_name.startswith("refs/heads/"):
            refs.append(ref_name.removeprefix("refs/heads/"))
        elif ref_name.startswith("refs/tags/"):
            refs.append(ref_name.removeprefix("refs/tags/"))
    refs.sort(key=len, reverse=True)
    return refs


async def _parse_github_tree_source(source: str) -> tuple[str, str, str]:
    parsed = urllib.parse.urlparse(source)
    parts = [p for p in parsed.path.split("/") if p]
    # /owner/repo/tree/<ref>/<path...>
    if len(parts) < 4 or parts[2] != "tree":
        raise ValueError("Invalid GitHub tree URL.")
    owner, repo = parts[0], parts[1]
    tail = "/".join(parts[3:])
    repo_url = f"https://github.com/{owner}/{repo}.git"
    refs = await _git_ls_remote_refs(repo_url)
    for candidate_ref in refs:
        if tail == candidate_ref:
            return repo_url, candidate_ref, ""
        prefix = f"{candidate_ref}/"
        if tail.startswith(prefix):
            return repo_url, candidate_ref, tail[len(prefix):]
    # Fallback when ref lookup is unavailable.
    first, _, rest = tail.partition("/")
    return repo_url, first, rest


async def normalize_skill_source(
    source: str,
    *,
    ref: str = "",
    subpath: str = "",
) -> SkillSourceSpec:
    raw = str(source or "").strip()
    if not raw:
        raise ValueError("Missing skill source.")
    source_type = _detect_source_type(raw)
    if source_type == "github_tree":
        repo_url, tree_ref, tree_subpath = await _parse_github_tree_source(raw)
        return SkillSourceSpec(
            original_source=raw,
            provider="github",
            source_type="git",
            source=repo_url,
            repo_url=repo_url,
            ref=ref or tree_ref,
            subpath=subpath or tree_subpath,
        )
    provider = "github" if "github.com" in raw else "generic"
    return SkillSourceSpec(
        original_source=raw,
        provider=provider,
        source_type=source_type,
        source=raw,
        repo_url=raw if source_type == "git" else "",
        ref=ref,
        subpath=subpath,
    )


def _iter_candidate_skill_dirs(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for skill_md in sorted(root.rglob("SKILL.md")):
        rel = skill_md.relative_to(root)
        if any(part in _SKILL_SKIP_DIRS for part in rel.parts):
            continue
        candidates.append(skill_md.parent)
    if not candidates:
        return []
    resolved = [c.resolve() for c in candidates]
    result: list[Path] = []
    for i, d in enumerate(resolved):
        if not any(j != i and str(d).startswith(str(other) + "/") for j, other in enumerate(resolved)):
            result.append(candidates[i])
    return result


def _read_plugin_manifest(manifest_path: Path) -> dict:
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _discover_plugin_skill_roots(root: Path) -> list[Path]:
    roots: list[Path] = []
    for marker in _PLUGIN_MANIFEST_DIRS:
        for manifest_path in root.rglob(f"{marker}/plugin.json"):
            rel = manifest_path.relative_to(root)
            if any(part in _SKILL_SKIP_DIRS for part in rel.parts):
                continue
            data = _read_plugin_manifest(manifest_path)
            skills_rel = str(data.get("skills") or "").strip()
            if not skills_rel:
                continue
            plugin_root = manifest_path.parent.parent
            skill_root = (plugin_root / skills_rel).resolve()
            if skill_root.exists():
                roots.append(skill_root)
    seen: set[str] = set()
    uniq: list[Path] = []
    for path in roots:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(path)
    return uniq


def _dedupe_candidate_dirs(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _extract_candidate_manifest(candidate_dir: Path, root: Path) -> dict:
    validator = SkillValidator()
    skill_md = candidate_dir / "SKILL.md"
    validation = validator.validate(skill_md)
    compat = validator.check_compatibility(skill_md)
    fm = validator.parse_frontmatter(skill_md)
    rel_path = candidate_dir.resolve().relative_to(root.resolve())
    scripts_dir = candidate_dir / "scripts"
    tools_dir = candidate_dir / "tools"
    requirements = candidate_dir / "requirements.txt"
    warnings = list(validation.warnings) + list(compat.all_warnings)
    if scripts_dir.is_dir() and not requirements.exists():
        warnings.append("Contains scripts/ but does not declare requirements.txt.")
    install_state = "installable"
    if warnings:
        install_state = "installable_with_warnings"
    if not validation.ok:
        install_state = "unsupported"
        warnings = list(dict.fromkeys([*warnings, *validation.errors]))
    return {
        "id": rel_path.as_posix() or ".",
        "path": rel_path.as_posix() or ".",
        "name": validation.name or fm.get("name") or candidate_dir.name,
        "description": validation.description or fm.get("description") or "",
        "version": validation.version or fm.get("version") or "",
        "install_state": install_state,
        "warnings": warnings,
        "signals": {
            "has_tools": tools_dir.is_dir(),
            "has_scripts": scripts_dir.is_dir(),
            "has_requirements": requirements.exists(),
            "has_include_files": bool(fm.get("include_files")),
        },
        "metadata": {
            "tags": fm.get("tags") or [],
            "homepage": fm.get("homepage") or "",
            "author": fm.get("author") or "",
            "license": fm.get("license") or "",
        },
    }


async def _clone_repo_to_temp(repo_url: str, *, ref: str = "") -> Path:
    temp_root = Path(tempfile.mkdtemp(prefix="hc-skill-src-"))
    repo_dir = temp_root / "repo"
    clone_cmd = ["git", "clone", "--depth=1"]
    if ref:
        clone_cmd += ["--branch", ref]
    clone_cmd += [repo_url, str(repo_dir)]
    proc = await asyncio.create_subprocess_exec(
        *clone_cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        deadline = asyncio.get_event_loop().time() + 120
        err_lines = await _stream_lines(proc.stderr, deadline=deadline)
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        shutil.rmtree(temp_root, ignore_errors=True)
        raise ValueError("Git clone timed out after 120 seconds.") from None
    if proc.returncode != 0:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise ValueError(err_lines[-1] if err_lines else "Git clone failed.")
    if ref and not (repo_dir / ".git").exists():
        return repo_dir
    if ref:
        # Best effort fallback for commit-ish refs.
        probe = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_dir), "rev-parse", "--verify", "HEAD",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await probe.wait()
    return repo_dir


async def acquire_skill_source_root(spec: SkillSourceSpec) -> tuple[Path, Path | None]:
    temp_root: Path | None = None
    if spec.source_type == "local_dir":
        return Path(spec.source).expanduser().resolve(), None
    if spec.source_type == "local_zip":
        temp_root = Path(tempfile.mkdtemp(prefix="hc-skill-src-"))
        zip_path = Path(spec.source).expanduser().resolve()
        if not zip_path.exists() or not zipfile.is_zipfile(zip_path):
            shutil.rmtree(temp_root, ignore_errors=True)
            raise ValueError("Local ZIP is missing or invalid.")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(temp_root)
        return temp_root, temp_root
    if spec.source_type == "https_zip":
        temp_root = Path(tempfile.mkdtemp(prefix="hc-skill-src-"))
        zip_file = temp_root / "download.zip"
        req = urllib.request.Request(
            spec.source,
            headers={"User-Agent": "hushclaw-skill-inspector/1.0"},
        )
        loop = asyncio.get_event_loop()
        raw_bytes = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=60).read()),
            timeout=65,
        )
        zip_file.write_bytes(raw_bytes)
        if not zipfile.is_zipfile(zip_file):
            shutil.rmtree(temp_root, ignore_errors=True)
            raise ValueError("Downloaded file is not a valid ZIP.")
        with zipfile.ZipFile(zip_file) as zf:
            zf.extractall(temp_root / "extracted")
        return temp_root / "extracted", temp_root
    if spec.source_type == "git":
        repo_root = await _clone_repo_to_temp(spec.source, ref=spec.ref)
        return repo_root, repo_root.parent
    raise ValueError(f"Unsupported skill source type: {spec.source_type}")


async def inspect_skill_source(
    source: str,
    *,
    ref: str = "",
    subpath: str = "",
) -> dict:
    spec = await normalize_skill_source(source, ref=ref, subpath=subpath)
    root, temp_root = await acquire_skill_source_root(spec)
    try:
        inspect_root = root
        plugin_roots = _discover_plugin_skill_roots(root)
        plugin_root_ids = {str(p.resolve()) for p in plugin_roots}
        if spec.subpath:
            inspect_root = (root / spec.subpath).resolve()
            if not inspect_root.exists():
                raise ValueError(f"Specified skill path not found: {spec.subpath}")
        candidates = _iter_candidate_skill_dirs(inspect_root)
        if not candidates and (inspect_root / "SKILL.md").exists():
            candidates = [inspect_root]
        if plugin_roots:
            plugin_candidates: list[Path] = []
            for plugin_root in plugin_roots:
                if plugin_root.exists():
                    plugin_candidates.extend(_iter_candidate_skill_dirs(plugin_root))
                    if (plugin_root / "SKILL.md").exists():
                        plugin_candidates.append(plugin_root)
            candidates = _dedupe_candidate_dirs([*candidates, *plugin_candidates])
        candidate_payloads = [_extract_candidate_manifest(path, root) for path in candidates]
        for candidate in candidate_payloads:
            full_path = (root / candidate["path"]).resolve()
            candidate["preferred"] = any(
                str(full_path) == skill_root or str(full_path).startswith(skill_root + "/")
                for skill_root in plugin_root_ids
            )
        candidate_payloads.sort(key=lambda item: (not item.get("preferred"), item["path"]))
        selected = None
        preferred_candidates = [item for item in candidate_payloads if item.get("preferred")]
        if spec.subpath:
            wanted = spec.subpath.strip("/").rstrip("/")
            for candidate in candidate_payloads:
                if candidate["path"].strip("./").rstrip("/") == wanted:
                    selected = candidate
                    break
        elif len(preferred_candidates) == 1:
            selected = preferred_candidates[0]
        elif len(candidate_payloads) == 1:
            selected = candidate_payloads[0]
        plugin_manifests: list[dict] = []
        for marker in _PLUGIN_MANIFEST_DIRS:
            for manifest_path in root.rglob(f"{marker}/plugin.json"):
                data = _read_plugin_manifest(manifest_path)
                if not data:
                    continue
                plugin_manifests.append({
                    "path": manifest_path.relative_to(root).as_posix(),
                    "name": data.get("name") or "",
                    "version": data.get("version") or "",
                    "description": data.get("description") or "",
                    "skills": data.get("skills") or "",
                    "homepage": data.get("homepage") or "",
                    "repository": data.get("repository") or "",
                    "keywords": data.get("keywords") or [],
                })
        warnings: list[str] = []
        if not candidate_payloads:
            warnings.append("No SKILL.md candidates were found in this source.")
        elif len(candidate_payloads) > 1 and not selected:
            warnings.append("Multiple skill candidates found. Pick one before installing.")
        if plugin_manifests and not any(item.get("preferred") for item in candidate_payloads):
            warnings.append("Plugin metadata was found, but no candidate mapped directly to its skills path.")
        return {
            "ok": bool(candidate_payloads),
            "source": spec.original_source,
            "provider": spec.provider,
            "source_type": spec.source_type,
            "repo_url": spec.repo_url or spec.source,
            "ref": spec.ref,
            "subpath": spec.subpath,
            "default_scope": "user",
            "candidates": candidate_payloads,
            "selected_candidate": selected,
            "plugin_manifests": plugin_manifests,
            "warnings": warnings,
        }
    finally:
        if temp_root is not None and temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)
