"""apply_patch — multi-file, multi-operation atomic text replacement tool."""
from __future__ import annotations

from pathlib import Path

from hushclaw.tools.base import tool, ToolResult


@tool(
    description=(
        "Apply multiple precise text replacements to one or more files in a single call. "
        "Each operation specifies a file path, the exact text to find (old), and its "
        "replacement (new). All 'old' strings must exist exactly once before any file is "
        "modified — the tool validates first, then applies atomically."
    ),
)
def apply_patch(operations: list) -> ToolResult:
    """
    Apply a list of text replacement operations across one or more files.

    Parameters
    ----------
    operations : list of dicts, each with keys:
        - path (str): file path (relative to cwd or absolute)
        - old  (str): exact string to replace (must appear exactly once)
        - new  (str): replacement string (empty string = deletion)

    Algorithm
    ---------
    1. Validate pass — read every referenced file; verify each ``old``
       string appears **exactly once**.  Collect all errors before returning.
    2. If any errors → return failure, zero files modified.
    3. Apply pass — mutate in-memory copies sequentially (each op sees the
       result of the previous one for the same file), then write to disk.
    """
    if not operations:
        return ToolResult.error("No operations provided.")

    # ── Validation pass ────────────────────────────────────────────────────
    file_contents: dict[str, str] = {}
    errors: list[str] = []

    for i, op in enumerate(operations):
        if not isinstance(op, dict):
            errors.append(
                f"Operation {i}: must be a dict with keys 'path', 'old', 'new'."
            )
            continue

        path_str = op.get("path", "")
        old_str = op.get("old", "")
        new_str = op.get("new", "")   # empty string is valid (deletion)

        if not path_str:
            errors.append(f"Operation {i}: 'path' is required.")
            continue
        if not isinstance(old_str, str):
            errors.append(f"Operation {i}: 'old' must be a string.")
            continue

        p = Path(path_str)
        if not p.is_absolute():
            p = Path.cwd() / p

        if str(p) not in file_contents:
            if not p.exists():
                errors.append(f"Operation {i}: file not found: {p}")
                continue
            try:
                file_contents[str(p)] = p.read_text(encoding="utf-8")
            except OSError as e:
                errors.append(f"Operation {i}: cannot read {p}: {e}")
                continue

        content = file_contents[str(p)]
        count = content.count(old_str)
        if count == 0:
            snippet = repr(old_str[:80])
            errors.append(
                f"Operation {i} ({p.name}): 'old' string not found: {snippet}"
            )
        elif count > 1:
            errors.append(
                f"Operation {i} ({p.name}): 'old' string appears {count} times "
                "(must be unique — provide more surrounding context)."
            )

    if errors:
        return ToolResult.error(
            "Validation failed — no files were modified:\n"
            + "\n".join(f"  • {e}" for e in errors)
        )

    # ── Apply pass ─────────────────────────────────────────────────────────
    # Each op sees the result of prior ops on the same file.
    working: dict[str, str] = dict(file_contents)

    for op in operations:
        path_str = op["path"]
        p = Path(path_str)
        if not p.is_absolute():
            p = Path.cwd() / p
        old_str = op["old"]
        new_str = op.get("new", "")
        working[str(p)] = working[str(p)].replace(old_str, new_str, 1)

    write_errors: list[str] = []
    written: list[str] = []
    for path_key, new_content in working.items():
        p = Path(path_key)
        try:
            p.write_text(new_content, encoding="utf-8")
            written.append(p.name)
        except OSError as e:
            write_errors.append(f"Write failed for {p}: {e}")

    if write_errors:
        return ToolResult.error(
            "Some files written but errors occurred:\n"
            + "\n".join(f"  • {e}" for e in write_errors)
            + f"\nWrote: {', '.join(written)}"
        )

    lines = [
        f"Applied {len(operations)} operation(s) to {len(written)} file(s):"
    ]
    for op in operations:
        p = Path(op["path"])
        old_preview = repr(op["old"][:40])
        new_preview = repr(op.get("new", "")[:40])
        lines.append(f"  • {p.name}: {old_preview} → {new_preview}")
    return ToolResult.ok("\n".join(lines))
