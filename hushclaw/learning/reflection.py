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


def extract_profile_updates(trace: TaskTrace) -> list[dict]:
    """Extract structured user profile facts from a turn — zero LLM calls.

    Called unconditionally every turn (not gated on should_reflect).
    Returns a list of profile fact dicts suitable for UserProfileStore.upsert_fact().
    """
    updates: list[dict] = []
    lower = (trace.user_input or "").lower()

    # ── Response length preference ──────────────────────────────────────────
    if any(tok in lower for tok in ("concise", "brief", "简洁", "简短", "精简", "简明")):
        updates.append({
            "category": "communication_style",
            "key": "response_depth",
            "value": {"value": "concise", "summary": "User prefers concise, to-the-point answers."},
            "confidence": 0.9,
        })
    if any(tok in lower for tok in ("详细", "thorough", "detailed", "in detail", "具体", "展开")):
        updates.append({
            "category": "communication_style",
            "key": "response_depth",
            "value": {"value": "detailed", "summary": "User prefers detailed, thorough answers."},
            "confidence": 0.9,
        })

    # ── Language preference ─────────────────────────────────────────────────
    if any(tok in lower for tok in ("用中文", "中文回答", "说中文", "请用中文", "以中文")):
        updates.append({
            "category": "communication_style",
            "key": "language",
            "value": {"value": "zh", "summary": "User prefers responses in Chinese."},
            "confidence": 0.9,
        })
    if any(tok in lower for tok in ("in english", "用英文", "英文回答", "please use english", "respond in english")):
        updates.append({
            "category": "communication_style",
            "key": "language",
            "value": {"value": "en", "summary": "User prefers responses in English."},
            "confidence": 0.9,
        })

    # ── Response format preference ──────────────────────────────────────────
    if any(tok in lower for tok in (
        "step by step", "step-by-step", "分步骤", "一步一步", "逐步", "step by step",
    )):
        updates.append({
            "category": "communication_style",
            "key": "response_style",
            "value": {"value": "step_by_step", "summary": "User prefers step-by-step explanations."},
            "confidence": 0.85,
        })
    if any(tok in lower for tok in ("bullet", "列表", "条目", "以列表", "用列表")):
        updates.append({
            "category": "communication_style",
            "key": "response_style",
            "value": {"value": "bullets", "summary": "User prefers bullet-point format."},
            "confidence": 0.8,
        })
    if any(tok in lower for tok in ("先给代码", "直接代码", "show code first", "code first")):
        updates.append({
            "category": "communication_style",
            "key": "response_style",
            "value": {"value": "code_first", "summary": "User wants code before explanation."},
            "confidence": 0.85,
        })

    # ── Git / workflow habits ───────────────────────────────────────────────
    if "commit & push" in lower or "commit and push" in lower:
        updates.append({
            "category": "workflow_habits",
            "key": "git_handoff",
            "value": {"value": "commit_and_push", "summary": "User expects commit and push after implementation."},
            "confidence": 0.85,
        })

    # ── Correction-driven: user is precise about output quality ────────────
    if trace.corrections:
        updates.append({
            "category": "communication_style",
            "key": "precision_signal",
            "value": {"value": "high", "summary": "User corrects misaligned responses; high precision expected."},
            "confidence": 0.7,
        })

    return updates


def reflect_trace(trace: TaskTrace) -> ReflectionResult:
    """Build a lightweight reflection without another LLM call.

    Called only when should_reflect() is True (3+ tools, errors, corrections, or skills).
    Profile updates are now handled separately by extract_profile_updates().
    """
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

    return ReflectionResult(
        success=success,
        outcome=outcome,
        failure_mode=failure_mode,
        lesson=lesson[:280],
        strategy_hint=strategy_hint[:280],
        skill_candidate=skill_candidate,
        profile_updates=[],  # profile updates handled by extract_profile_updates()
    )
