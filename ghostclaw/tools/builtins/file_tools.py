"""File system tools: read and write files."""
from __future__ import annotations

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
