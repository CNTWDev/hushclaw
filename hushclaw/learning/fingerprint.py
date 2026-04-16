"""Heuristic task fingerprinting for lightweight learning-loop grouping."""
from __future__ import annotations


def fingerprint_task(user_input: str, tool_names: list[str] | None = None) -> str:
    text = (user_input or "").lower()
    names = set(tool_names or [])
    if {"fetch_url", "jina_read", "browser_navigate", "browser_get_content"} & names:
        if any(tok in text for tok in ("compare", "vs", "对比", "竞品")):
            return "web_research_comparison"
        return "web_research"
    if {"read_file", "write_file", "apply_patch", "run_shell"} & names:
        if any(tok in text for tok in ("test", "fix", "bug", "报错", "修")):
            return "code_fix_iteration"
        return "code_change"
    if {"remember_skill", "use_skill"} & names:
        return "skill_workflow"
    if any(tok in text for tok in ("ppt", "slides", "brief", "report", "简报", "汇报")):
        return "deliverable_generation"
    if any(tok in text for tok in ("remember", "memory", "profile", "偏好", "记忆")):
        return "memory_management"
    return "general_assistance"
