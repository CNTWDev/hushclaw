"""Knowledge base tools: index local documents for semantic recall."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from hushclaw.tools.base import tool, ToolResult

if TYPE_CHECKING:
    from hushclaw.memory.store import MemoryStore

# File extensions treated as plain text (no optional deps required)
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".text",
    ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".env",
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".html", ".htm", ".xml",
    ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".log",
}

_MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB hard cap per file


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_EXTENSIONS


def _walk_files(root: Path, glob_pattern: str) -> list[Path]:
    """Recursively find files matching glob_pattern under root."""
    if "**" in glob_pattern:
        return sorted(root.rglob(glob_pattern.lstrip("**/").lstrip("/")))
    return sorted(root.glob(glob_pattern))


def _ingest_directory(
    root: Path,
    glob_pattern: str,
    scope: str,
    chunk_size: int,
    overlap: int,
    memory_store: "MemoryStore",
) -> ToolResult:
    """Shared logic for indexing a directory of files."""
    files = _walk_files(root, glob_pattern)
    text_files = [f for f in files if f.is_file() and _is_text_file(f)]

    if not text_files:
        return ToolResult.ok(
            f"No matching text files found under {root} with pattern '{glob_pattern}'"
        )

    indexed = 0
    skipped = 0
    failed = 0
    failed_names: list[str] = []

    for fp in text_files:
        if fp.stat().st_size > _MAX_FILE_BYTES:
            failed += 1
            failed_names.append(f"{fp.name} (too large)")
            continue
        try:
            result = memory_store.ingest_file(
                fp, scope=scope, chunk_size=chunk_size, overlap=overlap
            )
            if result.get("skipped"):
                skipped += 1
            else:
                indexed += 1
        except Exception as exc:
            failed += 1
            failed_names.append(f"{fp.name}: {exc}")

    parts = [
        f"Indexed {indexed} file(s)",
        f"{skipped} unchanged (skipped)",
    ]
    if failed:
        parts.append(f"{failed} failed: {', '.join(failed_names[:5])}")
    return ToolResult.ok(". ".join(parts) + ". Use recall() to query the indexed content.")


@tool(
    name="index_directory",
    description=(
        "Index local files into the knowledge base so they can be recalled by `recall()`. "
        "path: directory to scan (required). "
        "glob_pattern: file matching pattern (default '**/*.md' — all Markdown files recursively). "
        "scope: 'global' for personal knowledge, 'workspace:<name>' for project docs. "
        "chunk_size: approximate token size per chunk (default 512). "
        "Returns a summary of how many files were indexed, skipped (unchanged), or failed."
    ),
)
def index_directory(
    path: str,
    glob_pattern: str = "**/*.md",
    scope: str = "global",
    chunk_size: int = 512,
    overlap: int = 64,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    """Recursively index a directory of text files into the knowledge base."""
    if _memory_store is None:
        return ToolResult.error("Memory store not available")

    root = Path(path).expanduser().resolve()
    if not root.exists():
        return ToolResult.error(f"Directory not found: {path}")
    if not root.is_dir():
        return ToolResult.error(f"Not a directory: {path}")

    return _ingest_directory(root, glob_pattern, scope, chunk_size, overlap, _memory_store)


@tool(
    name="list_indexed_docs",
    description=(
        "List all documents that have been indexed into the knowledge base. "
        "scope: filter by scope ('global', 'workspace:<name>'). Leave empty to list all. "
        "Returns file paths, chunk counts, and when they were last indexed."
    ),
    parallel_safe=True,
)
def list_indexed_docs(
    scope: str = "",
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    """List indexed document sources."""
    if _memory_store is None:
        return ToolResult.error("Memory store not available")

    sources = _memory_store.list_document_sources(scope=scope or None)
    if not sources:
        msg = f"No indexed documents in scope '{scope}'" if scope else "No documents indexed yet"
        return ToolResult.ok(msg)

    import datetime
    lines: list[str] = []
    for s in sources:
        ts = datetime.datetime.fromtimestamp(s["updated"]).strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"• {s['file_path']}  ({s['chunk_count']} chunks, scope={s['scope']}, updated={ts})"
        )
    return ToolResult.ok(f"{len(sources)} indexed document(s):\n" + "\n".join(lines))


@tool(
    name="refresh_index",
    description=(
        "Re-index a previously indexed file or directory if its contents have changed. "
        "source_path: file path or directory to refresh. "
        "For a directory, re-indexes all matching files. For a single file, re-indexes just that file. "
        "Unchanged files are skipped automatically (content hash check)."
    ),
)
def refresh_index(
    source_path: str,
    glob_pattern: str = "**/*.md",
    scope: str = "global",
    chunk_size: int = 512,
    overlap: int = 64,
    _memory_store: "MemoryStore | None" = None,
) -> ToolResult:
    """Re-index a file or directory, updating changed content."""
    if _memory_store is None:
        return ToolResult.error("Memory store not available")

    p = Path(source_path).expanduser().resolve()
    if not p.exists():
        return ToolResult.error(f"Path not found: {source_path}")

    if p.is_dir():
        return _ingest_directory(p, glob_pattern, scope, chunk_size, overlap, _memory_store)

    if not _is_text_file(p):
        return ToolResult.error(
            f"Unsupported file type: {p.suffix}. "
            f"Supported: {', '.join(sorted(_TEXT_EXTENSIONS))}"
        )
    if p.stat().st_size > _MAX_FILE_BYTES:
        return ToolResult.error(f"{p.name} exceeds the 2 MB limit")

    try:
        result = _memory_store.ingest_file(
            p, scope=scope, chunk_size=chunk_size, overlap=overlap
        )
    except Exception as exc:
        return ToolResult.error(f"Failed to index {p.name}: {exc}")

    if result.get("skipped"):
        return ToolResult.ok(f"{p.name}: no changes detected, skipped.")
    return ToolResult.ok(
        f"{p.name}: re-indexed into {result['chunk_count']} chunk(s)."
    )
