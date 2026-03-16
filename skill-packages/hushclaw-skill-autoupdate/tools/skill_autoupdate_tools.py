"""Skill Auto-Update tools — scan, diagnose, and patch installed HushClaw skills.

No extra dependencies required beyond stdlib.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

from hushclaw.tools.base import ToolResult, tool


@tool(description="List all installed skill packages in skill_dir. Returns name, version, has_tools.")
def autoupdate_list_skills(skill_dir: str) -> ToolResult:
    """Scan skill_dir for hushclaw-skill-* directories and read their SKILL.md metadata."""
    base = Path(skill_dir).expanduser()
    if not base.exists():
        return ToolResult(error=f"Directory not found: {skill_dir}")

    skills = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.exists():
            continue
        content = skill_md.read_text(encoding="utf-8")
        name = _fm_field(content, "name") or d.name
        version = _fm_field(content, "version") or "unknown"
        has_tools = (d / "tools").is_dir() and any((d / "tools").glob("*.py"))
        skills.append({"name": name, "dir": str(d), "version": version, "has_tools": has_tools})

    return ToolResult(output={"skill_dir": str(base), "count": len(skills), "skills": skills})


@tool(description=(
    "Try to import all modules inside a skill's tools directory. "
    "Returns {ok: bool, missing_deps: [], import_errors: []}."
))
def autoupdate_check_imports(tools_file: str) -> ToolResult:
    """Parse a tools.py file and attempt to import its top-level dependencies."""
    p = Path(tools_file).expanduser()
    if not p.exists():
        return ToolResult(error=f"File not found: {tools_file}")

    source = p.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return ToolResult(output={"ok": False, "syntax_error": str(e), "missing_deps": [], "import_errors": []})

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])

    missing: list[str] = []
    errors: list[str] = []
    for mod in set(imports):
        if mod in sys.stdlib_module_names:  # type: ignore[attr-defined]
            continue
        spec = importlib.util.find_spec(mod)
        if spec is None:
            missing.append(mod)

    return ToolResult(output={
        "file": str(p),
        "ok": len(missing) == 0 and len(errors) == 0,
        "missing_deps": missing,
        "import_errors": errors,
        "all_imports": sorted(set(imports)),
    })


@tool(description="Read the last tail_lines lines of a log file and return lines containing 'error' or 'skill'.")
def autoupdate_read_log(log_path: str, tail_lines: int = 200) -> ToolResult:
    """Tail a log file and filter lines relevant to skill errors."""
    p = Path(log_path).expanduser()
    if not p.exists():
        return ToolResult(error=f"Log file not found: {log_path}")

    all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = all_lines[-tail_lines:]
    relevant = [l for l in tail if any(k in l.lower() for k in ("error", "skill", "import", "traceback", "exception"))]
    return ToolResult(output={
        "log_path": str(p),
        "total_lines": len(all_lines),
        "scanned_lines": len(tail),
        "relevant_lines": relevant,
    })


@tool(description="Install a Python package using pip into the current environment.")
def autoupdate_pip_install(package: str, _confirm_fn=None) -> ToolResult:
    """Run pip install <package>."""
    if _confirm_fn and not _confirm_fn(f"Install package via pip: {package}?"):
        return ToolResult(error="Cancelled by user.")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", package],
        capture_output=True, text=True,
    )
    return ToolResult(output={
        "package": package,
        "success": r.returncode == 0,
        "output": (r.stdout + r.stderr).strip()[-1000:],
    })


@tool(description=(
    "Apply an exact string replacement patch to a file. "
    "Shows diff summary and requires user confirmation."
))
def autoupdate_apply_patch(
    file_path: str,
    old_str: str,
    new_str: str,
    _confirm_fn=None,
) -> ToolResult:
    """Replace old_str with new_str in file_path after user confirmation."""
    p = Path(file_path).expanduser()
    if not p.exists():
        return ToolResult(error=f"File not found: {file_path}")

    content = p.read_text(encoding="utf-8")
    if old_str not in content:
        return ToolResult(error="old_str not found in file — patch cannot be applied.")

    diff = f"- {old_str!r}\n+ {new_str!r}"
    if _confirm_fn and not _confirm_fn(f"Apply patch to {p.name}?\n{diff}"):
        return ToolResult(error="Cancelled by user.")

    new_content = content.replace(old_str, new_str, 1)
    p.write_text(new_content, encoding="utf-8")
    return ToolResult(output={"patched": True, "file": str(p), "diff_summary": diff})


@tool(description="Check a Python file for syntax errors using py_compile.")
def autoupdate_run_syntax_check(file_path: str) -> ToolResult:
    """Return {ok: bool, error: str} for the given .py file."""
    p = Path(file_path).expanduser()
    if not p.exists():
        return ToolResult(error=f"File not found: {file_path}")
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(p)],
        capture_output=True, text=True,
    )
    return ToolResult(output={
        "file": str(p),
        "ok": r.returncode == 0,
        "error": (r.stdout + r.stderr).strip() or None,
    })


@tool(description="Run git pull in a skill directory to fetch upstream updates.")
def autoupdate_git_pull(skill_dir: str) -> ToolResult:
    """Execute git pull in the given directory."""
    d = Path(skill_dir).expanduser()
    if not d.exists():
        return ToolResult(error=f"Directory not found: {skill_dir}")
    r = subprocess.run(["git", "pull"], cwd=str(d), capture_output=True, text=True)
    return ToolResult(output={
        "skill_dir": str(d),
        "success": r.returncode == 0,
        "output": (r.stdout + r.stderr).strip(),
    })


# ── helpers ──────────────────────────────────────────────────────────────────

def _fm_field(content: str, field: str) -> str | None:
    """Extract a YAML front-matter field value (simple key: value)."""
    for line in content.splitlines():
        if line.startswith(field + ":"):
            return line[len(field) + 1:].strip().strip('"').strip("'")
    return None
