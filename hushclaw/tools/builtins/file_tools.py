"""File system tools: read and write files."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

from hushclaw.tools.base import tool, ToolResult


def _build_download_meta(filename: str, display_name: str, _config=None) -> dict:
    """Build normalized download metadata for web UI consumption."""
    rel_url = f"/files/{filename}"
    meta = {
        "trusted": True,
        "url": rel_url,
        "name": display_name,
        "file_id": filename.split("_", 1)[0] if "_" in filename else "",
    }
    base_url = ""
    if _config is not None:
        base_url = str(getattr(_config.server, "public_base_url", "") or "").strip()
    if base_url:
        parsed = urlparse(base_url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            meta["absolute_url"] = f"{base_url.rstrip('/')}{rel_url}"
    return meta


def register_download_path(path: str | Path, _config=None, display_name: str = "") -> dict:
    """Copy a local file into upload_dir and return normalized download metadata."""
    from uuid import uuid4

    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"File not found: {src}")
    if not src.is_file():
        raise IsADirectoryError(f"Not a file: {src}")

    upload_dir: Path | None = None
    if _config is not None:
        upload_dir = getattr(_config.server, "upload_dir", None)
    if upload_dir is None:
        raise ValueError("upload_dir not configured — cannot generate download URL")
    upload_dir = Path(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # If the file is already inside upload_dir, reuse it instead of copying again.
    try:
        if src.resolve().parent == upload_dir.resolve():
            return _build_download_meta(src.name, display_name or src.name, _config=_config)
    except Exception:
        pass

    safe_name = re.sub(r"[^\w.\-]", "_", display_name or src.name)[:128] or "file"
    file_id = uuid4().hex[:12]
    filename = f"{file_id}_{safe_name}"
    dest = upload_dir / filename
    shutil.copy2(src, dest)
    return _build_download_meta(filename, safe_name, _config=_config)


@tool(
    name="read_file",
    description="Read the contents of a file at the specified path.",
    parallel_safe=True,
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
    description=(
        "Write content to a file at the specified path. "
        "Use paths inside the user's home directory (e.g. ~/documents/report.md) or "
        "relative paths. Do NOT use /files/ as a path — that is a URL prefix, not a "
        "filesystem directory. To make a file downloadable after writing, call "
        "make_download_url with the same path."
    ),
)
def write_file(path: str, content: str, _config=None) -> ToolResult:
    """Write content to a file."""
    import re as _re

    try:
        p = Path(path).expanduser()

        # Intercept paths that start with /files/ — these are URL prefixes, not
        # real filesystem paths. Normalize to upload_dir and return an explicit
        # download URL so callers don't keep using a mismatched path.
        if path.startswith("/files/"):
            upload_dir: Path | None = None
            if _config is not None:
                upload_dir = getattr(_config.server, "upload_dir", None)
            if upload_dir is None:
                return ToolResult.error(
                    "'/files/' is a download URL prefix, not a writable filesystem path. "
                    "Use a path like ~/filename.md instead."
                )
            upload_dir = Path(upload_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)
            filename = _re.sub(r"[^\w.\-/]", "_", path[len("/files/"):]).lstrip("/") or "file"
            # Keep /files storage flat to align with server file serving behavior.
            safe_name = Path(filename).name
            p = upload_dir / safe_name

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        if path.startswith("/files/"):
            meta = _build_download_meta(p.name, p.name, _config=_config)
            payload = {
                "ok": True,
                "written_chars": len(content),
                "path": str(p),
                # Keep top-level download fields aligned with make_download_url.
                "trusted": meta.get("trusted", True),
                "url": meta.get("url", ""),
                "name": meta.get("name", p.name),
                "file_id": meta.get("file_id", ""),
                "download": meta,
            }
            if "absolute_url" in meta:
                payload["absolute_url"] = meta["absolute_url"]
            return ToolResult.ok(json.dumps(payload, ensure_ascii=False))
        return ToolResult.ok(f"Written {len(content)} characters to {p}")
    except PermissionError:
        return ToolResult.error(f"Permission denied: {path}")
    except Exception as e:
        return ToolResult.error(f"Failed to write {path}: {e}")


@tool(
    name="list_dir",
    description="List files and directories at a given path.",
    parallel_safe=True,
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
    parallel_safe=True,
)
def make_download_url(path: str, _config=None) -> ToolResult:
    """Copy a file to the upload directory and return a /files/ download URL."""
    try:
        payload = register_download_path(path, _config=_config)
        return ToolResult.ok(json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        return ToolResult.error(f"Failed to register file: {e}")
