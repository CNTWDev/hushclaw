"""Heuristic reflection artifacts for the learning loop."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Structural role extractor ──────────────────────────────────────────────────
# Matches "我是 [optional company/qualifier] [title]" or "i'm a [title]"
_ROLE_RE = re.compile(
    r"(?:我是|i'?m\s+a?n?\s*|i\s+am\s+a?n?\s*)"
    r"(?:[\w\s\u4e00-\u9fff]{0,25}?)"
    r"(gm|ceo|cto|cpo|vp|总监|总经理|总裁|产品经理|pm(?=\b|，|,|。|$)|负责人"
    r"|director|manager|strategist|策略师"
    r"|backend|frontend|fullstack|full[\s\-]stack"
    r"|后端|前端|全栈|架构师|ml\s+engineer|ai\s+engineer)",
    re.IGNORECASE,
)
_ROLE_VALUE_MAP: dict[str, str] = {
    "gm": "general_manager",
    "总经理": "general_manager",
    "总裁": "general_manager",
    "ceo": "ceo",
    "cto": "cto",
    "cpo": "cpo",
    "vp": "vp",
    "总监": "director",
    "director": "director",
    "产品经理": "product_manager",
    "pm": "product_manager",
    "manager": "manager",
    "strategist": "strategist",
    "策略师": "strategist",
    "负责人": "lead",
    "backend": "backend_engineer",
    "后端": "backend_engineer",
    "frontend": "frontend_engineer",
    "前端": "frontend_engineer",
    "fullstack": "fullstack_engineer",
    "full stack": "fullstack_engineer",
    "全栈": "fullstack_engineer",
    "架构师": "architect",
    "ml engineer": "ml_engineer",
    "ai engineer": "ai_engineer",
}

# Matches "负责/主导 X" — captures the responsibility domain
_RESPONSIBILITY_RE = re.compile(
    r"(?:负责|主导|我做)\s*([\u4e00-\u9fff\w\s]{2,30}?)(?=[，,。.\n]|$)",
    re.IGNORECASE,
)

# Domain interest signals
_DOMAIN_SIGNALS: list[tuple[list[str], str]] = [
    (["ai战略", "ai 战略", "ai strategy", "人工智能战略", "ai战略规划"], "ai_strategy"),
    (["ai产品", "ai 产品", "ai product", "ai产品规划", "ai产品设计"], "ai_product"),
    (["大模型", "llm", "language model", "基础模型", "foundation model"], "llm"),
    (["智能硬件", "ai硬件", "ai hardware"], "hardware"),
    (["新硬件", "新形态", "硬件形态", "hardware form factor", "ai原生硬件", "硬件创新"], "hardware_innovation"),
    (["竞争策略", "差异化", "asymmetric", "非对称", "competitive strategy", "竞争格局", "不对称"], "strategy"),
    (["商业模式", "business model", "盈利模式"], "business_model"),
]


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

    # ── Communication style: response depth ────────────────────────────────
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

    # ── Communication style: language ───────────────────────────────────────
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

    # ── Communication style: format ─────────────────────────────────────────
    if any(tok in lower for tok in (
        "step by step", "step-by-step", "分步骤", "一步一步", "逐步",
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

    # ── Communication style: formality ──────────────────────────────────────
    if any(tok in lower for tok in ("正式", "formal tone", "professional tone", "用正式语气", "正式一点")):
        updates.append({
            "category": "communication_style",
            "key": "formality",
            "value": {"value": "formal", "summary": "User prefers formal, professional tone."},
            "confidence": 0.85,
        })
    if any(tok in lower for tok in ("随意", "casual", "轻松点", "informal", "不用正式", "放松点")):
        updates.append({
            "category": "communication_style",
            "key": "formality",
            "value": {"value": "casual", "summary": "User prefers casual, relaxed tone."},
            "confidence": 0.82,
        })

    # ── Communication style: directness ─────────────────────────────────────
    if any(tok in lower for tok in (
        "直接给结论", "直接给答案", "直接说结论", "just tell me", "just give me",
        "bottom line", "cut to the chase", "just answer", "直接回答",
    )):
        updates.append({
            "category": "communication_style",
            "key": "directness",
            "value": {"value": "direct", "summary": "User wants direct answers without preamble."},
            "confidence": 0.90,
        })

    # ── Expertise: role / identity ───────────────────────────────────────────
    # Structural regex: "我是 [optional_company] [title]" / "i'm a [title]"
    role_match = _ROLE_RE.search(lower)
    if role_match:
        raw = role_match.group(1).lower().strip()
        role_value = _ROLE_VALUE_MAP.get(raw) or _ROLE_VALUE_MAP.get(raw.replace("-", " ")) or raw
        updates.append({
            "category": "expertise",
            "key": "role",
            "value": {"value": role_value, "summary": f"User's role: {role_value.replace('_', ' ')}."},
            "confidence": 0.85,
        })
    # Skip basics — advanced user
    if any(tok in lower for tok in (
        "不要解释基础", "跳过入门", "我知道基础", "don't explain basics",
        "skip the basics", "assume i know", "skip the intro", "no need to explain",
        "我懂基础", "不需要解释基础", "不用解释基础", "基础不用讲",
    )):
        updates.append({
            "category": "expertise",
            "key": "assume_basics",
            "value": {"value": "true", "summary": "User is experienced; skip basic explanations."},
            "confidence": 0.88,
        })
    # Beginner/learning signals
    if any(tok in lower for tok in (
        "我是新手", "i'm a beginner", "初学者", "i'm learning", "刚开始学",
        "i'm not familiar", "我不太熟", "我不懂这个", "help me understand the basics",
    )):
        updates.append({
            "category": "expertise",
            "key": "level",
            "value": {"value": "learning", "summary": "User is still learning this area; include explanations."},
            "confidence": 0.75,
        })
    # Responsibility focus area: "负责/主导 X"
    resp_match = _RESPONSIBILITY_RE.search(trace.user_input or "")
    if resp_match:
        focus = resp_match.group(1).strip()[:60]
        if len(focus) >= 2:
            updates.append({
                "category": "expertise",
                "key": "focus_area",
                "value": {"value": focus, "summary": f"User is responsible for: {focus}"},
                "confidence": 0.75,
            })

    # ── Domains of interest ──────────────────────────────────────────────────
    for tokens, domain in _DOMAIN_SIGNALS:
        if any(tok in lower for tok in tokens):
            updates.append({
                "category": "domains_of_interest",
                "key": domain,
                "value": {"value": domain.replace("_", " "), "summary": f"User has interest in: {domain.replace('_', ' ')}."},
                "confidence": 0.80,
            })

    # ── Thinking style & strategic preferences ───────────────────────────────
    if any(tok in lower for tok in (
        "喜欢思考深入", "深度思考", "深入思考", "think deeply", "depth of thinking",
        "系统性思考", "第一性原理", "first principles",
    )):
        updates.append({
            "category": "preferences",
            "key": "thinking_style",
            "value": {"value": "deep", "summary": "User prefers deep, systematic thinking."},
            "confidence": 0.78,
        })
    if any(tok in lower for tok in (
        "更全面", "全局观", "看全局", "全局思维", "holistic", "big picture",
        "看得更全", "全面思考", "comprehensive view",
    )):
        updates.append({
            "category": "preferences",
            "key": "thinking_style",
            "value": {"value": "holistic", "summary": "User prefers holistic, big-picture perspective."},
            "confidence": 0.78,
        })
    if any(tok in lower for tok in (
        "差异化竞争", "差异化策略", "differentiated", "differentiation",
        "非对称策略", "非对称竞争", "asymmetric strategy", "asymmetric competition",
        "错位竞争",
    )):
        updates.append({
            "category": "preferences",
            "key": "strategy_approach",
            "value": {"value": "asymmetric_differentiation",
                      "summary": "User favors asymmetric / differentiated competitive strategy."},
            "confidence": 0.80,
        })

    # ── Avoidances ──────────────────────────────────────────────────────────
    if any(tok in lower for tok in (
        "不要总结你做了什么", "stop summarizing what you did", "don't summarize what you just did",
        "i can read the diff", "我能看到改了什么", "不要说你做了什么", "别总结",
    )):
        updates.append({
            "category": "avoidances",
            "key": "trailing_summary",
            "value": {"value": "avoid", "summary": "User dislikes trailing summaries of actions just taken."},
            "confidence": 0.92,
        })
    if any(tok in lower for tok in (
        "不要加注释", "no comments", "don't add comments", "remove comments",
        "去掉注释", "不加注释", "不要写注释",
    )):
        updates.append({
            "category": "avoidances",
            "key": "code_comments",
            "value": {"value": "avoid", "summary": "User prefers code without inline comments."},
            "confidence": 0.88,
        })
    if any(tok in lower for tok in (
        "no disclaimers", "不要免责声明", "stop warning me", "no caveats",
        "不要提醒我注意", "不要加警告", "skip the warnings",
    )):
        updates.append({
            "category": "avoidances",
            "key": "disclaimers",
            "value": {"value": "avoid", "summary": "User does not want disclaimers or caution warnings."},
            "confidence": 0.85,
        })
    if any(tok in lower for tok in (
        "不要废话", "别废话", "no preamble", "skip the preamble",
        "stop the fluff", "不要说废话", "别啰嗦",
    )):
        updates.append({
            "category": "avoidances",
            "key": "preamble",
            "value": {"value": "avoid", "summary": "User dislikes verbose preamble before the actual answer."},
            "confidence": 0.88,
        })

    # ── Workflow habits: git ─────────────────────────────────────────────────
    if "commit & push" in lower or "commit and push" in lower:
        updates.append({
            "category": "workflow_habits",
            "key": "git_handoff",
            "value": {"value": "commit_and_push", "summary": "User expects commit and push after implementation."},
            "confidence": 0.85,
        })

    # ── Tooling preferences ──────────────────────────────────────────────────
    # Test frameworks
    for fw in ("pytest", "jest", "vitest", "mocha", "unittest"):
        if fw in lower:
            updates.append({
                "category": "tooling_preferences",
                "key": "test_framework",
                "value": {"value": fw, "summary": f"User prefers {fw} for testing."},
                "confidence": 0.85,
            })
            break  # only one test framework per turn
    # Language preference
    if any(tok in lower for tok in ("用 typescript", "prefer typescript", "typescript not javascript", "ts not js", "用typescript")):
        updates.append({
            "category": "tooling_preferences",
            "key": "language_pref",
            "value": {"value": "typescript", "summary": "User prefers TypeScript over JavaScript."},
            "confidence": 0.87,
        })
    if any(tok in lower for tok in ("用 python", "prefer python", "python only", "用python")):
        updates.append({
            "category": "tooling_preferences",
            "key": "language_pref",
            "value": {"value": "python", "summary": "User prefers Python."},
            "confidence": 0.82,
        })
    # Package managers
    if any(tok in lower for tok in ("用 poetry", "use poetry", "用poetry")):
        updates.append({
            "category": "tooling_preferences",
            "key": "pkg_manager",
            "value": {"value": "poetry", "summary": "User uses Poetry for Python packaging."},
            "confidence": 0.85,
        })
    if any(tok in lower for tok in ("用 yarn", "prefer yarn", "yarn not npm")):
        updates.append({
            "category": "tooling_preferences",
            "key": "pkg_manager",
            "value": {"value": "yarn", "summary": "User prefers Yarn over npm."},
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
