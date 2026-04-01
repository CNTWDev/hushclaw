"""Deck spec and consulting-QC tools for high-quality PPT generation."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hushclaw.tools.base import ToolResult, tool

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "ppt-deck-v1.2.schema.json"
_PROFILE_DIR = Path(__file__).resolve().parent.parent / "schema" / "story_profiles"
_WEAK_HEADLINE_PATTERNS = [
    r"^\s*(市场分析|项目进展|现状分析|总结|报告|Overview)\s*$",
]
_CHAPTER_ORDER = [
    "summary",
    "starting_point",
    "strategy_house",
    "initiative_deep_dive",
    "implementation_roadmap",
]
_IMPLEMENTATION_TEMPLATES = {
    "initiative_case_card",
    "implementation_requirements_panel",
    "roadmap_quarterly",
    "decision_next_steps",
}


def _json_ok(data: dict[str, Any]) -> ToolResult:
    return ToolResult.ok(json.dumps(data, ensure_ascii=False, indent=2))


def _parse_json(payload: str) -> tuple[dict[str, Any] | None, str]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"
    if not isinstance(data, dict):
        return None, "Top-level JSON value must be an object."
    return data, ""


def _load_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_profiles() -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    if not _PROFILE_DIR.exists():
        return profiles
    for p in _PROFILE_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                name = str(data.get("name") or p.stem)
                profiles[name] = data
        except Exception:
            continue
    return profiles


def _rule_source_required(slide: dict[str, Any]) -> bool:
    headline = str(slide.get("headline", ""))
    proof_blocks = slide.get("proof_blocks") or []
    has_number = bool(re.search(r"\d", headline))
    for b in proof_blocks:
        if isinstance(b, dict):
            val = b.get("value")
            if isinstance(val, (int, float)):
                has_number = True
            elif isinstance(val, str) and re.search(r"\d", val):
                has_number = True
    if not has_number:
        return True
    return bool(slide.get("source_refs"))


def _rule_answer_first(slide: dict[str, Any]) -> bool:
    headline = str(slide.get("headline", "")).strip()
    if len(headline) < 8:
        return False
    for p in _WEAK_HEADLINE_PATTERNS:
        if re.search(p, headline, flags=re.IGNORECASE):
            return False
    return True


def _rule_one_message(slide: dict[str, Any]) -> bool:
    headline = str(slide.get("headline", ""))
    separators = headline.count("；") + headline.count(";") + headline.count(" and ")
    return separators <= 1


def _validate_page_mode(deck_meta: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    page_mode = deck_meta.get("page_mode")
    if page_mode == "fixed" and "page_count" not in deck_meta:
        issues.append(
            {"code": "QC_201_PAGE_MODE_FIXED_MISSING_COUNT", "message": "page_mode=fixed requires page_count"}
        )
    if page_mode == "range":
        min_v = deck_meta.get("page_count_min")
        max_v = deck_meta.get("page_count_max")
        if min_v is None or max_v is None:
            issues.append(
                {"code": "QC_202_PAGE_MODE_RANGE_MISSING_BOUNDS", "message": "page_mode=range requires min/max"}
            )
        elif isinstance(min_v, int) and isinstance(max_v, int) and min_v > max_v:
            issues.append(
                {"code": "QC_203_PAGE_MODE_RANGE_INVALID_BOUNDS", "message": "page_count_min cannot exceed page_count_max"}
            )
    return issues


def _validate_with_jsonschema(data: dict[str, Any]) -> list[dict[str, str]]:
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return [
            {
                "code": "QC_901_MISSING_DEPENDENCY",
                "message": "jsonschema is not installed; run pip install jsonschema",
            }
        ]

    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    out: list[dict[str, str]] = []
    for e in errors[:50]:
        path = ".".join([str(x) for x in e.path]) or "$"
        out.append({"code": "QC_101_SCHEMA_INVALID", "message": f"{path}: {e.message}"})
    return out


def _check_chapter_sequence(slides: list[dict[str, Any]]) -> bool:
    seen: list[int] = []
    for s in slides:
        tag = s.get("chapter_tag")
        if tag in _CHAPTER_ORDER:
            seen.append(_CHAPTER_ORDER.index(tag))
    if not seen:
        return True
    return seen == sorted(seen)


def _check_strategy_house_completeness(data: dict[str, Any]) -> bool:
    slides = data.get("slides") or []
    strategy_heavy = False
    for s in slides:
        if not isinstance(s, dict):
            continue
        if s.get("chapter_tag") == "strategy_house" or s.get("template") == "strategy_house_overview":
            strategy_heavy = True
            break
    if not strategy_heavy:
        return True
    sh = data.get("strategy_house")
    if not isinstance(sh, dict):
        return False
    required = ["aspiration", "objectives", "initiatives", "enablers", "foundation"]
    for k in required:
        v = sh.get(k)
        if isinstance(v, list) and len(v) == 0:
            return False
        if isinstance(v, str) and not v.strip():
            return False
        if v is None:
            return False
    return True


def _check_implementation_readiness(slide: dict[str, Any]) -> bool:
    needs_impl = (
        slide.get("chapter_tag") == "implementation_roadmap"
        or slide.get("template") in _IMPLEMENTATION_TEMPLATES
    )
    if not needs_impl:
        return True
    impl = slide.get("implementation")
    if not isinstance(impl, dict):
        return False
    owner = str(impl.get("owner", "")).strip()
    timeline = str(impl.get("timeline", "")).strip()
    success_kpis = impl.get("success_kpis") or []
    next_steps = impl.get("next_steps") or []
    return bool(owner and timeline and len(success_kpis) > 0 and len(next_steps) > 0)


@tool(description="Return the built-in consulting deck JSON schema used for universal PPT specification.")
def pptx_get_deck_schema() -> ToolResult:
    schema = _load_schema()
    return _json_ok(schema)


@tool(description="List available story profiles used for profile-driven deck skeleton generation.")
def pptx_list_story_profiles() -> ToolResult:
    profiles = _load_profiles()
    items = []
    for name, data in sorted(profiles.items()):
        items.append(
            {
                "name": name,
                "description": data.get("description", ""),
                "recommended_page_counts": data.get("recommended_page_counts", []),
            }
        )
    return _json_ok({"profiles": items})


@tool(
    description=(
        "Generate a chaptered slide skeleton from a story profile. "
        "page_mode: fixed|auto|range. For fixed mode, page_count is required."
    )
)
def pptx_recommend_slides_by_profile(
    profile_name: str = "berry_business_strategy",
    page_mode: str = "auto",
    page_count: int = 5,
    page_count_min: int = 3,
    page_count_max: int = 10,
) -> ToolResult:
    profiles = _load_profiles()
    profile = profiles.get(profile_name)
    if profile is None:
        return ToolResult.error(f"Profile not found: {profile_name}")

    skeletons = profile.get("skeletons") or {}
    if page_mode == "fixed":
        if page_count < 3:
            return ToolResult.error("page_count must be >= 3 in fixed mode")
        key = str(page_count)
        if key in skeletons:
            chosen = skeletons[key]
        else:
            # Fallback: select nearest available and trim/extend.
            avail = sorted([int(x) for x in skeletons.keys() if str(x).isdigit()])
            if not avail:
                return ToolResult.error("Profile does not provide any skeleton.")
            nearest = min(avail, key=lambda x: abs(x - page_count))
            chosen = list(skeletons[str(nearest)])
            while len(chosen) > page_count:
                chosen.pop()
            while len(chosen) < page_count:
                chosen.append({"chapter_tag": "implementation_roadmap", "template": "decision_next_steps"})
    elif page_mode == "range":
        target = max(page_count_min, min(page_count_max, 5))
        return pptx_recommend_slides_by_profile(
            profile_name=profile_name,
            page_mode="fixed",
            page_count=target,
        )
    else:  # auto
        default_count = 5
        rec = profile.get("recommended_page_counts") or []
        if rec:
            default_count = int(rec[0] if 5 not in rec else 5)
        return pptx_recommend_slides_by_profile(
            profile_name=profile_name,
            page_mode="fixed",
            page_count=default_count,
        )

    slides = []
    chapter_to_id = {tag: f"ch{i+1}" for i, tag in enumerate(_CHAPTER_ORDER)}
    chapter_titles = {
        "summary": "Summary",
        "starting_point": "Starting Point",
        "strategy_house": "Strategy House",
        "initiative_deep_dive": "Initiatives",
        "implementation_roadmap": "Roadmap",
    }
    for i, sk in enumerate(chosen):
        tag = sk.get("chapter_tag", "summary")
        sid = f"s{i + 1:02d}"
        slides.append(
            {
                "id": sid,
                "chapter_id": chapter_to_id.get(tag, "ch1"),
                "chapter_tag": tag,
                "template": sk.get("template", "exec_summary_3proof"),
                "headline": f"[{sid}] Insert conclusion headline",
                "so_what": "[Insert implication and decision/action]",
                "proof_blocks": [],
                "visual_spec": {"visual_type": "none", "chart_type": "none"},
                "qc": {"must_pass": ["answer_first", "one_message_per_slide", "actionability_required"]},
            }
        )

    chapters = []
    for tag in _CHAPTER_ORDER:
        if any(s.get("chapter_tag") == tag for s in slides):
            chapters.append(
                {"id": chapter_to_id[tag], "name": chapter_titles[tag], "intent": f"Deliver {chapter_titles[tag]} story block"}
            )

    out = {
        "deck_meta": {
            "title": "Profile-driven strategy deck",
            "language": "zh-CN",
            "audience_level": "exec",
            "objective_type": "decide",
            "page_mode": "fixed",
            "page_count": len(slides),
            "theme": "consulting_clean",
            "story_profile_name": profile_name,
        },
        "storyline": {
            "core_answer": "[Insert core answer]",
            "narrative_framework": "scqa",
            "chapters": chapters,
        },
        "slides": slides,
    }
    return _json_ok(out)


@tool(
    description=(
        "Validate a consulting deck JSON payload against the universal schema and page-mode rules. "
        "Input must be a JSON string."
    )
)
def pptx_validate_deck_spec(deck_json: str) -> ToolResult:
    data, err = _parse_json(deck_json)
    if data is None:
        return ToolResult.error(err)

    issues = _validate_with_jsonschema(data)
    if not issues:
        deck_meta = data.get("deck_meta")
        if isinstance(deck_meta, dict):
            issues.extend(_validate_page_mode(deck_meta))
        else:
            issues.append({"code": "QC_102_MISSING_DECK_META", "message": "deck_meta must be an object"})

    return _json_ok({"valid": len(issues) == 0, "issues": issues})


@tool(
    description=(
        "Run consulting-style quality checks and return a scored report with error codes. "
        "Input must be a JSON string conforming to the deck schema."
    )
)
def pptx_run_consulting_qc(deck_json: str) -> ToolResult:
    data, err = _parse_json(deck_json)
    if data is None:
        return ToolResult.error(err)

    schema_issues = _validate_with_jsonschema(data)
    if schema_issues:
        return _json_ok(
            {
                "score_total": 0,
                "pass": False,
                "fatal_issues": schema_issues,
                "major_issues": [],
                "minor_issues": [],
                "rewrite_priority_order": [],
            }
        )

    slides = data.get("slides") or []
    fatal: list[dict[str, str]] = []
    major: list[dict[str, str]] = []
    minor: list[dict[str, str]] = []

    for idx, slide in enumerate(slides):
        if not isinstance(slide, dict):
            fatal.append({"code": "QC_103_SLIDE_TYPE_INVALID", "message": f"slides[{idx}] must be object"})
            continue
        sid = str(slide.get("id", f"s{idx:02d}"))
        if not _rule_answer_first(slide):
            major.append({"code": "QC_001_HEADLINE_NOT_ANSWER_FIRST", "message": f"{sid}: headline is weak/non-conclusive"})
        if not _rule_one_message(slide):
            major.append({"code": "QC_002_MULTI_MESSAGE_HEADLINE", "message": f"{sid}: headline likely contains multiple messages"})
        if not _rule_source_required(slide):
            fatal.append({"code": "QC_003_SOURCE_REQUIRED", "message": f"{sid}: numeric claim requires source_refs"})
        so_what = str(slide.get("so_what", "")).strip()
        if len(so_what) < 6:
            minor.append({"code": "QC_004_SO_WHAT_TOO_WEAK", "message": f"{sid}: so_what is too short/weak"})
        proof_blocks = slide.get("proof_blocks") or []
        if slide.get("template") != "title_opening" and len(proof_blocks) == 0:
            major.append({"code": "QC_005_EVIDENCE_MISSING", "message": f"{sid}: non-title slide should include proof_blocks"})
        if not _check_implementation_readiness(slide):
            major.append({"code": "QC_007_IMPLEMENTATION_READINESS_MISSING", "message": f"{sid}: implementation fields missing required details"})

    slide_objs = [s for s in slides if isinstance(s, dict)]
    if not _check_chapter_sequence(slide_objs):
        major.append({"code": "QC_006_CHAPTER_SEQUENCE_INVALID", "message": "chapter_tag order must follow summary->starting_point->strategy_house->initiative_deep_dive->implementation_roadmap"})
    if not _check_strategy_house_completeness(data):
        major.append({"code": "QC_008_STRATEGY_HOUSE_INCOMPLETE", "message": "strategy_house requires aspiration/objectives/initiatives/enablers/foundation for strategy-heavy decks"})

    score = 100 - len(fatal) * 15 - len(major) * 8 - len(minor) * 3
    if score < 0:
        score = 0
    passed = score >= 85 and len(fatal) == 0

    priority: list[str] = []
    for group in (fatal, major):
        for issue in group:
            m = re.search(r"^(s\d{2,3})", issue.get("message", ""))
            if m and m.group(1) not in priority:
                priority.append(m.group(1))

    return _json_ok(
        {
            "score_total": score,
            "pass": passed,
            "fatal_issues": fatal,
            "major_issues": major,
            "minor_issues": minor,
            "rewrite_priority_order": priority,
        }
    )
