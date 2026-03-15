"""File system tools: read and write files."""
from __future__ import annotations

import shutil
from pathlib import Path

from ghostclaw.tools.base import tool, ToolResult


@tool(
    name="read_file",
    description="Read the contents of a file at the specified path.",
)
def read_file(path: str, max_bytes: int = 32768) -> ToolResult:
    """Read a file and return its contents."""
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return ToolResult.error(f"File not found: {path}")
        if not p.is_file():
            return ToolResult.error(f"Not a file: {path}")
        size = p.stat().st_size
        if size > max_bytes:
            content = p.read_bytes()[:max_bytes].decode("utf-8", errors="replace")
            return ToolResult.ok(f"[Truncated to {max_bytes} bytes]\n{content}")
        return ToolResult.ok(p.read_text(encoding="utf-8", errors="replace"))
    except PermissionError:
        return ToolResult.error(f"Permission denied: {path}")
    except Exception as e:
        return ToolResult.error(f"Failed to read {path}: {e}")


@tool(
    name="write_file",
    description="Write content to a file at the specified path.",
)
def write_file(path: str, content: str) -> ToolResult:
    """Write content to a file."""
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return ToolResult.ok(f"Written {len(content)} characters to {path}")
    except PermissionError:
        return ToolResult.error(f"Permission denied: {path}")
    except Exception as e:
        return ToolResult.error(f"Failed to write {path}: {e}")


@tool(
    name="list_dir",
    description="List files and directories at a given path.",
)
def list_dir(path: str = ".") -> ToolResult:
    """List directory contents."""
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return ToolResult.error(f"Path not found: {path}")
        if not p.is_dir():
            return ToolResult.error(f"Not a directory: {path}")
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
        lines = []
        for entry in entries:
            kind = "F" if entry.is_file() else "D"
            lines.append(f"[{kind}] {entry.name}")
        return ToolResult.ok("\n".join(lines) if lines else "(empty)")
    except Exception as e:
        return ToolResult.error(f"Failed to list {path}: {e}")


@tool(
    name="make_download_url",
    description=(
        "Register a local file for download through the web UI and return its /files/ URL. "
        "Use this after writing a file so the user can download it."
    ),
)
def make_download_url(path: str, _config=None) -> ToolResult:
    """Copy a file to the upload directory and return a /files/ download URL."""
    import re
    from uuid import uuid4

    try:
        src = Path(path).expanduser()
        if not src.exists():
            return ToolResult.error(f"File not found: {path}")
        if not src.is_file():
            return ToolResult.error(f"Not a file: {path}")

        # Determine upload_dir from config
        upload_dir: Path | None = None
        if _config is not None:
            upload_dir = getattr(_config.server, "upload_dir", None)
        if upload_dir is None:
            return ToolResult.error("upload_dir not configured — cannot generate download URL")
        upload_dir = Path(upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)

        safe_name = re.sub(r"[^\w.\-]", "_", src.name)[:128] or "file"
        file_id = uuid4().hex[:12]
        filename = f"{file_id}_{safe_name}"
        dest = upload_dir / filename
        shutil.copy2(src, dest)

        return ToolResult.ok({
            "url": f"/files/{filename}",
            "name": safe_name,
            "file_id": file_id,
        })
    except Exception as e:
        return ToolResult.error(f"Failed to register file: {e}")
