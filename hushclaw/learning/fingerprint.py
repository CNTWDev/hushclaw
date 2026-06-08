"""Structural task fingerprinting for lightweight learning-loop grouping."""
from __future__ import annotations


def fingerprint_task(user_input: str, tool_names: list[str] | None = None) -> str:
    names = set(tool_names or [])
    if {"fetch_url", "jina_read", "browser_navigate", "browser_get_content"} & names:
        return "web_research"
    if {"read_file", "write_file", "apply_patch", "run_shell"} & names:
        return "code_change"
    if {"remember_skill", "use_skill"} & names:
        return "skill_workflow"
    return "general_assistance"
