"""Deck spec and consulting-QC tools for high-quality PPT generation."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hushclaw.tools.base import ToolResult, tool

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "ppt-deck-v1.2.schema.json"
_WEAK_HEADLINE_PATTERNS = [
    r"^\s*(市场分析|项目进展|现状分析|总结|报告|Overview)\s*$",
]


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
    # Simple practical proxy: avoid stacked clauses in the headline.
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


@tool(description="Return the built-in consulting deck JSON schema used for universal PPT specification.")
def pptx_get_deck_schema() -> ToolResult:
    schema = _load_schema()
    return _json_ok(schema)


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

    score = 100 - len(fatal) * 15 - len(major) * 8 - len(minor) * 3
    if score < 0:
        score = 0
    passed = score >= 85 and len(fatal) == 0

    # prioritize slides with fatal/major issues first
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
