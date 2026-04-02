"""Skill Builder tools — scaffold and save new HushClaw skill packages.

No extra dependencies required.
"""
from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

from hushclaw.tools.base import ToolResult, tool


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")


@tool(description=(
    "Generate SKILL.md content and a tools.py skeleton for a new skill. "
    "workflow_steps is a list of plain-English step descriptions. "
    "tools_needed is a list of tool names (can be empty for prompt-only skills)."
))
def skillbuild_scaffold(
    name: str,
    description: str,
    workflow_steps: list[str],
    tools_needed: list[str] | None = None,
    author: str = "HushClaw",
    tags: list[str] | None = None,
) -> ToolResult:
    """Return generated SKILL.md text and tools.py skeleton."""
    slug = _slugify(name)
    tag_list = json_safe_list(tags or [slug])
    has_tools = bool(tools_needed)

    # Build SKILL.md
    steps_md = "\n".join(f"{i+1}. {s}" for i, s in enumerate(workflow_steps))
    tool_list_md = ""
    if tools_needed:
        tool_list_md = "\n可用工具：\n\n" + "\n".join(f"- `{t}(...)` — TODO: describe" for t in tools_needed) + "\n"

    skill_md = textwrap.dedent(f"""\
        ---
        name: {slug}
        description: {description}
        tags: {tag_list}
        author: {author}
        version: "1.0.0"
        has_tools: {str(has_tools).lower()}
        ---

        你是 {name} 专家。
        {tool_list_md}
        工作流程：

        {steps_md}
    """)

    # Build tools.py skeleton
    tools_py = ""
    if tools_needed:
        fn_defs = []
        for t in tools_needed:
            fn_defs.append(textwrap.dedent(f"""\
                @tool(description="TODO: describe what {t} does.")
                def {t}(param: str) -> ToolResult:
                    \"\"\"TODO: implement {t}.\"\"\"
                    return ToolResult.ok({{"result": param}})
            """))
        tools_py = textwrap.dedent(f"""\
            \"\"\"Tools for the {name} skill.\"\"\"
            from __future__ import annotations
            from hushclaw.tools.base import ToolResult, tool


            """) + "\n\n".join(fn_defs)

    return ToolResult.ok({
        "skill_md": skill_md,
        "tools_py": tools_py,
        "slug": slug,
        "has_tools": has_tools,
    })


@tool(description=(
    "Save a skill package to disk at output_dir/hushclaw-skill-{slug}/. "
    "Pass skill_md_content and optionally tools_py_content and requirements (list of pip packages)."
))
def skillbuild_save(
    output_dir: str,
    slug: str,
    skill_md_content: str,
    tools_py_content: str = "",
    requirements: list[str] | None = None,
) -> ToolResult:
    """Write SKILL.md, tools/tools.py, and requirements.txt to disk."""
    base = Path(output_dir).expanduser() / f"hushclaw-skill-{slug}"
    base.mkdir(parents=True, exist_ok=True)

    (base / "SKILL.md").write_text(skill_md_content, encoding="utf-8")
    written = ["SKILL.md"]

    if tools_py_content:
        tools_dir = base / "tools"
        tools_dir.mkdir(exist_ok=True)
        (tools_dir / f"{slug.replace('-', '_')}_tools.py").write_text(tools_py_content, encoding="utf-8")
        written.append(f"tools/{slug.replace('-', '_')}_tools.py")

    if requirements:
        (base / "requirements.txt").write_text("\n".join(requirements) + "\n", encoding="utf-8")
        written.append("requirements.txt")

    return ToolResult.ok({
        "skill_dir": str(base),
        "files_written": written,
    })


@tool(description="Validate a skill directory: check SKILL.md front-matter and tools.py syntax.")
def skillbuild_validate(skill_dir: str) -> ToolResult:
    """Run basic checks on a skill package directory."""
    base = Path(skill_dir).expanduser()
    issues: list[str] = []

    skill_md = base / "SKILL.md"
    if not skill_md.exists():
        issues.append("SKILL.md not found")
    else:
        content = skill_md.read_text()
        for field in ("name:", "description:", "version:"):
            if field not in content:
                issues.append(f"SKILL.md missing front-matter field: {field}")

    for tools_file in (base / "tools").glob("*.py") if (base / "tools").exists() else []:
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(tools_file)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            issues.append(f"Syntax error in {tools_file.name}: {r.stderr.strip()}")

    return ToolResult.ok({
        "skill_dir": str(base),
        "valid": len(issues) == 0,
        "issues": issues,
    })


def json_safe_list(lst: list[str]) -> str:
    import json
    return json.dumps(lst)
