"""Best-effort verifier for file-mutating tool calls."""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


_FILE_TOOL_NAMES = {"write_file", "edit_document", "patch", "apply_patch"}


@dataclass(slots=True)
class FileSnapshot:
    path: str
    exists: bool
    size: int = 0
    mtime_ns: int = 0
    sha256: str = ""


@dataclass(slots=True)
class FileDiagnostic:
    path: str
    ok: bool
    checker: str
    message: str = ""


@dataclass(slots=True)
class FileMutationSummary:
    tool_name: str
    files: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def should_verify_tool(tool_name: str) -> bool:
    return tool_name in _FILE_TOOL_NAMES


def candidate_paths(tool_name: str, arguments: dict[str, Any], *, workspace_dir: Path | None = None) -> list[Path]:
    paths: list[str] = []
    if tool_name in {"write_file", "edit_document"}:
        value = arguments.get("path")
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    elif tool_name in {"patch", "apply_patch"}:
        for op in arguments.get("operations") or []:
            if isinstance(op, dict) and isinstance(op.get("path"), str):
                paths.append(op["path"].strip())

    out: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (workspace_dir or Path.cwd()) / p
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p.absolute()
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            out.append(resolved)
    return out


def snapshot(path: Path) -> FileSnapshot:
    try:
        st = path.stat()
    except OSError:
        return FileSnapshot(path=str(path), exists=False)
    digest = ""
    if path.is_file() and st.st_size <= 5_000_000:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            digest = ""
    return FileSnapshot(
        path=str(path),
        exists=True,
        size=int(st.st_size),
        mtime_ns=int(st.st_mtime_ns),
        sha256=digest,
    )


def verify_mutation(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    workspace_dir: Path | None = None,
    before: dict[str, FileSnapshot] | None = None,
) -> FileMutationSummary | None:
    paths = candidate_paths(tool_name, arguments, workspace_dir=workspace_dir)
    if not paths:
        return None
    before = before or {}
    files: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for path in paths:
        prior = before.get(str(path)) or FileSnapshot(path=str(path), exists=False)
        after = snapshot(path)
        changed = (
            prior.exists != after.exists
            or prior.size != after.size
            or prior.mtime_ns != after.mtime_ns
            or (prior.sha256 and after.sha256 and prior.sha256 != after.sha256)
        )
        files.append({
            "path": str(path),
            "exists": after.exists,
            "changed": changed,
            "size": after.size,
            "before_size": prior.size,
            "sha256": after.sha256,
        })
        if after.exists and path.is_file():
            diag = diagnose_file(path)
            if diag is not None:
                diagnostics.append(asdict(diag))
    return FileMutationSummary(tool_name=tool_name, files=files, diagnostics=diagnostics)


def diagnose_file(path: Path) -> FileDiagnostic | None:
    suffix = path.suffix.lower()
    try:
        if suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"))
            return FileDiagnostic(str(path), True, "python-ast")
        if suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
            return FileDiagnostic(str(path), True, "json")
        if suffix in {".js", ".mjs", ".cjs"} and shutil.which("node"):
            proc = subprocess.run(
                ["node", "--check", str(path)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            return FileDiagnostic(
                str(path),
                proc.returncode == 0,
                "node --check",
                (proc.stderr or proc.stdout or "").strip(),
            )
    except Exception as exc:
        return FileDiagnostic(str(path), False, suffix.lstrip(".") or "file", str(exc))
    return None
