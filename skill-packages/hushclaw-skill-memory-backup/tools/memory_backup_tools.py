"""Memory backup and export tools for HushClaw.

Exports memories (notes/) to local Markdown/JSON or pushes them to a Git repo.
No extra dependencies required — uses stdlib only.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from hushclaw.tools.base import ToolResult, tool


def _default_notes_dir() -> Path:
    if platform.system() == "Darwin":
        return Path("~/Library/Application Support/hushclaw/notes").expanduser()
    return Path("~/.hushclaw/notes").expanduser()


@tool(description="List all memory note files. Returns file count and total size.")
def memory_list_notes(notes_dir: str = "") -> ToolResult:
    """List notes in the HushClaw notes directory."""
    base = Path(notes_dir).expanduser() if notes_dir else _default_notes_dir()
    if not base.exists():
        return ToolResult(output={"notes_dir": str(base), "count": 0, "files": []})

    files = sorted(base.rglob("*.md"))
    total_bytes = sum(f.stat().st_size for f in files)
    return ToolResult(output={
        "notes_dir": str(base),
        "count": len(files),
        "total_kb": round(total_bytes / 1024, 1),
        "files": [str(f.relative_to(base)) for f in files],
    })


@tool(description="Export all memory notes as Markdown files to output_dir. Creates the directory if needed.")
def memory_export_markdown(output_dir: str, notes_dir: str = "") -> ToolResult:
    """Copy the entire notes directory tree to output_dir."""
    base = Path(notes_dir).expanduser() if notes_dir else _default_notes_dir()
    dest = Path(output_dir).expanduser()

    if not base.exists():
        return ToolResult(error=f"Notes directory not found: {base}")

    dest.mkdir(parents=True, exist_ok=True)
    files = list(base.rglob("*.md"))
    if not files:
        return ToolResult(output={"exported": 0, "output_dir": str(dest)})

    for f in files:
        rel = f.relative_to(base)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)

    return ToolResult(output={
        "exported": len(files),
        "output_dir": str(dest),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    })


@tool(description="Export all memory notes to a single JSON file at output_path.")
def memory_export_json(output_path: str, notes_dir: str = "") -> ToolResult:
    """Serialize all notes (path + content + mtime) to one JSON file."""
    base = Path(notes_dir).expanduser() if notes_dir else _default_notes_dir()
    dest = Path(output_path).expanduser()

    if not base.exists():
        return ToolResult(error=f"Notes directory not found: {base}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    for f in sorted(base.rglob("*.md")):
        records.append({
            "path": str(f.relative_to(base)),
            "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
            "content": f.read_text(encoding="utf-8"),
        })

    dest.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return ToolResult(output={
        "exported": len(records),
        "output_path": str(dest),
        "size_kb": round(dest.stat().st_size / 1024, 1),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    })


@tool(description=(
    "Git-backup the notes directory: init repo if needed, commit all changes, "
    "then push to remote_url (optional). "
    "commit_message defaults to 'memory backup YYYY-MM-DD'."
))
def memory_git_backup(
    notes_dir: str = "",
    remote_url: str = "",
    commit_message: str = "",
) -> ToolResult:
    """Run git init/add/commit/push inside the notes directory."""
    base = Path(notes_dir).expanduser() if notes_dir else _default_notes_dir()
    if not base.exists():
        return ToolResult(error=f"Notes directory not found: {base}")

    msg = commit_message or f"memory backup {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    def _run(cmd: list[str]) -> tuple[int, str]:
        r = subprocess.run(cmd, cwd=str(base), capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr).strip()

    steps: list[str] = []

    # init
    code, out = _run(["git", "init"])
    steps.append(f"git init: {out or 'ok'}")

    # set remote if provided and not already set
    if remote_url:
        code2, _ = _run(["git", "remote", "get-url", "origin"])
        if code2 != 0:
            _run(["git", "remote", "add", "origin", remote_url])
        else:
            _run(["git", "remote", "set-url", "origin", remote_url])
        steps.append(f"remote set to {remote_url}")

    # add all
    code, out = _run(["git", "add", "-A"])
    steps.append(f"git add: {out or 'ok'}")

    # commit
    code, out = _run(["git", "commit", "-m", msg])
    if code != 0 and "nothing to commit" in out:
        steps.append("nothing to commit — already up to date")
        return ToolResult(output={"status": "up_to_date", "steps": steps, "notes_dir": str(base)})
    steps.append(f"git commit: {out.splitlines()[0] if out else 'ok'}")

    # push
    if remote_url:
        code, out = _run(["git", "push", "-u", "origin", "HEAD"])
        steps.append(f"git push: {'ok' if code == 0 else out}")

    return ToolResult(output={
        "status": "success",
        "commit_message": msg,
        "notes_dir": str(base),
        "steps": steps,
    })
