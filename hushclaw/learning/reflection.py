"""Heuristic reflection artifacts for the learning loop."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class TaskTrace:
    session_id: str
    user_input: str
    assistant_response: str
    tool_trace: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    corrections: list[str] = field(default_factory=list)
    used_skills: list[str] = field(default_factory=list)
    workspace: str = ""
    turn_count: int = 0
    task_fingerprint: str = ""


@dataclass(slots=True)
class ReflectionResult:
    success: bool
    outcome: str
    failure_mode: str
    lesson: str
    strategy_hint: str
    skill_candidate: dict | None = None
    profile_updates: list[dict] = field(default_factory=list)


def reflect_trace(trace: TaskTrace) -> ReflectionResult:
    """Build a lightweight reflection without another LLM call."""
    success = bool(trace.assistant_response.strip()) and not trace.errors
    if trace.errors:
        failure_mode = "; ".join(e[:180] for e in trace.errors[:2])
    elif trace.corrections:
        failure_mode = "User correction signal observed"
    else:
        failure_mode = ""

    outcome = (trace.assistant_response or "").strip()
    if not outcome and trace.tool_trace:
        last_tool = trace.tool_trace[-1]
        outcome = f"Completed tool workflow via {last_tool.get('tool_name', 'tool')}."
    outcome = outcome[:260]

    if trace.used_skills:
        lesson = (
            f"Skill-assisted execution via {', '.join(trace.used_skills[:2])} "
            f"worked best for {trace.task_fingerprint or 'this task type'}."
        )
    elif trace.errors and trace.assistant_response.strip():
        lesson = "The task succeeded after recovering from an intermediate tool or execution failure."
    elif trace.corrections:
        lesson = "The turn contained a user correction; future runs should align more closely with the user's stated preference."
    else:
        lesson = "The task completed without notable errors; preserve the successful workflow for similar future tasks."

    if trace.tool_trace:
        ordered = []
        for item in trace.tool_trace:
            name = str(item.get("tool_name") or "")
            if name and name not in ordered:
                ordered.append(name)
        strategy_hint = "Preferred tool flow: " + " -> ".join(ordered[:5])
    else:
        strategy_hint = "No durable tool workflow identified."

    skill_candidate = None
    if len(trace.tool_trace) >= 3 and not trace.errors:
        skill_candidate = {
            "mode": "create" if not trace.used_skills else "patch",
            "name_hint": trace.task_fingerprint or "workflow-skill",
            "observation": lesson,
            "workflow": strategy_hint,
        }

    profile_updates: list[dict] = []
    lower = (trace.user_input or "").lower()
    if any(tok in lower for tok in ("concise", "brief", "简洁", "简短")):
        profile_updates.append({
            "category": "communication_style",
            "key": "response_depth",
            "value": {"value": "concise", "summary": "User prefers concise answers."},
            "confidence": 0.9,
        })
    if any(tok in lower for tok in ("详细", "thorough", "detailed")):
        profile_updates.append({
            "category": "communication_style",
            "key": "response_depth",
            "value": {"value": "detailed", "summary": "User prefers detailed answers when needed."},
            "confidence": 0.9,
        })
    if "commit & push" in lower or "commit and push" in lower:
        profile_updates.append({
            "category": "workflow_habits",
            "key": "git_handoff",
            "value": {"value": "commit_and_push", "summary": "User expects commit and push after implementation."},
            "confidence": 0.85,
        })

    return ReflectionResult(
        success=success,
        outcome=outcome,
        failure_mode=failure_mode,
        lesson=lesson[:280],
        strategy_hint=strategy_hint[:280],
        skill_candidate=skill_candidate,
        profile_updates=profile_updates,
    )
