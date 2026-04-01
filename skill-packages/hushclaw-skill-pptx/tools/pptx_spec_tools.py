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
_RICHNESS_TEMPLATES = {"title_opening"}
_INDUSTRY_PRESETS: dict[str, dict[str, Any]] = {
    "generic": {
        "core_answer_suffix": "通过阶段化执行在可控风险下实现可验证业务结果。",
        "kpi_label": "核心KPI改善",
        "kpi_value": "12%",
        "benchmark_label": "行业对标差距",
        "benchmark_value": "8%",
        "objectives": ["增长质量提升", "效率持续优化", "风险可控执行"],
        "initiatives": ["聚焦高价值场景", "优化关键流程", "强化数据化运营"],
        "enablers": ["组织能力建设", "数据与技术底座", "治理与激励机制"],
        "requirements": ["预算批准", "跨部门协同机制", "周度复盘机制"],
    },
    "b2b_saas": {
        "core_answer_suffix": "通过产品商业化与销售效率联动，在12个月内提升ARR质量与续费稳定性。",
        "kpi_label": "ARR增长率",
        "kpi_value": "18%",
        "benchmark_label": "Logo churn差距",
        "benchmark_value": "5pp",
        "objectives": ["提升净收入留存", "优化获客回本周期", "强化产品价值实现"],
        "initiatives": ["分层定价升级", "高风险客户预警", "客户成功剧本化运营"],
        "enablers": ["产品数据埋点体系", "RevOps流程", "客户健康度模型"],
        "requirements": ["定价策略评审", "CSM编制保障", "销售激励重构"],
    },
    "cross_border_ecommerce": {
        "core_answer_suffix": "围绕选品、投放与履约效率联动，实现规模增长与利润率双提升。",
        "kpi_label": "GMV增长率",
        "kpi_value": "22%",
        "benchmark_label": "履约成本差距",
        "benchmark_value": "7%",
        "objectives": ["提升高毛利品类占比", "降低获客成本波动", "改善履约时效"],
        "initiatives": ["爆品组合优化", "渠道投放精细化", "仓配网络调优"],
        "enablers": ["跨境供应链协同", "广告归因体系", "库存预测模型"],
        "requirements": ["重点市场预算锁定", "物流伙伴SLA升级", "周度经营看板上线"],
    },
    "manufacturing_transformation": {
        "core_answer_suffix": "通过产线数字化与运营机制升级，提升OEE并降低单位成本。",
        "kpi_label": "OEE提升",
        "kpi_value": "10pp",
        "benchmark_label": "单位成本差距",
        "benchmark_value": "6%",
        "objectives": ["提升产线稳定性", "降低质量损耗", "缩短交付周期"],
        "initiatives": ["关键工序数字化", "预测性维护", "质量闭环管理"],
        "enablers": ["工业数据平台", "班组能力建设", "精益治理机制"],
        "requirements": ["试点产线资源保障", "设备改造窗口协调", "跨部门项目办公室"],
    },
    "ai_productization": {
        "core_answer_suffix": "通过场景优先级和模型工程化并行推进，形成可规模化商业价值。",
        "kpi_label": "AI功能渗透率",
        "kpi_value": "30%",
        "benchmark_label": "单位推理成本差距",
        "benchmark_value": "12%",
        "objectives": ["提升高价值场景命中率", "降低模型服务成本", "强化质量与安全合规"],
        "initiatives": ["场景分层路线图", "模型路由与缓存优化", "评测与监控体系建设"],
        "enablers": ["特征与知识底座", "MLOps平台", "AI治理规范"],
        "requirements": ["跨职能AI小组", "推理预算上限机制", "灰度发布与回滚策略"],
    },
}
_BRAND_STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "consulting_clean": {
        "palette": ["#0F172A", "#1D4ED8", "#94A3B8", "#E2E8F0"],
        "font_family": "Calibri",
        "headline_size_pt": 30,
        "body_size_pt": 16,
        "icon_style": "outline",
        "layout_density": "balanced",
        "visual_tone": "boardroom",
    },
    "mckinsey_like": {
        "palette": ["#112A46", "#1F5A96", "#6B7280", "#E5E7EB"],
        "font_family": "Arial",
        "headline_size_pt": 32,
        "body_size_pt": 16,
        "icon_style": "outline",
        "layout_density": "airy",
        "visual_tone": "boardroom",
    },
    "bain_like": {
        "palette": ["#9F1239", "#DC2626", "#334155", "#F1F5F9"],
        "font_family": "Arial",
        "headline_size_pt": 31,
        "body_size_pt": 16,
        "icon_style": "filled",
        "layout_density": "balanced",
        "visual_tone": "transformation",
    },
    "bcg_like": {
        "palette": ["#0B3A2B", "#16A34A", "#475569", "#E2E8F0"],
        "font_family": "Calibri",
        "headline_size_pt": 31,
        "body_size_pt": 16,
        "icon_style": "duotone",
        "layout_density": "balanced",
        "visual_tone": "growth",
    },
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


def _check_logic_chain(slide: dict[str, Any]) -> bool:
    if slide.get("template") == "title_opening":
        return True
    chain = slide.get("logic_chain")
    if not isinstance(chain, dict):
        return False
    claim = str(chain.get("claim", "")).strip()
    because = chain.get("because") or []
    therefore = str(chain.get("therefore", "")).strip()
    return bool(claim and isinstance(because, list) and len(because) > 0 and therefore)


def _check_visual_richness(slide: dict[str, Any]) -> bool:
    if slide.get("template") in _RICHNESS_TEMPLATES:
        return True
    visual = slide.get("visual_spec")
    if not isinstance(visual, dict):
        return False
    return str(visual.get("visual_type", "none")) != "none"


def _check_icon_plan(slide: dict[str, Any]) -> bool:
    if slide.get("template") in _RICHNESS_TEMPLATES:
        return True
    tokens = slide.get("design_tokens")
    if not isinstance(tokens, dict):
        return False
    icons = tokens.get("icon_keywords") or []
    style = str(tokens.get("icon_style", "")).strip()
    return isinstance(icons, list) and len(icons) > 0 and bool(style)


def _check_proof_density(slide: dict[str, Any]) -> bool:
    template = str(slide.get("template", ""))
    if template == "title_opening":
        return True
    proof_blocks = slide.get("proof_blocks") or []
    return len(proof_blocks) >= 2


def _chapter_action_hint(tag: str) -> str:
    mapping = {
        "summary": "优先方案并立即立项",
        "starting_point": "统一现状口径并锁定核心问题",
        "strategy_house": "对齐战略屋并明确牵引目标",
        "initiative_deep_dive": "聚焦高价值举措并锁定 owner",
        "implementation_roadmap": "确认里程碑并启动执行闭环",
    }
    return mapping.get(tag, "形成可执行决策")


def _get_preset(name: str) -> dict[str, Any]:
    key = str(name or "generic").strip().lower()
    return _INDUSTRY_PRESETS.get(key, _INDUSTRY_PRESETS["generic"])


def _get_brand_style(name: str) -> dict[str, Any]:
    key = str(name or "consulting_clean").strip().lower()
    return _BRAND_STYLE_PRESETS.get(key, _BRAND_STYLE_PRESETS["consulting_clean"])


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
    chapter_visual = {
        "summary": ("chart", "bar"),
        "starting_point": ("table", "none"),
        "strategy_house": ("matrix", "none"),
        "initiative_deep_dive": ("cards", "none"),
        "implementation_roadmap": ("process", "none"),
    }
    chapter_icon = {
        "summary": ["north_star", "kpi", "decision"],
        "starting_point": ["baseline", "gap", "risk"],
        "strategy_house": ["target", "pillar", "foundation"],
        "initiative_deep_dive": ["initiative", "value", "owner"],
        "implementation_roadmap": ["timeline", "milestone", "execution"],
    }
    for i, sk in enumerate(chosen):
        tag = sk.get("chapter_tag", "summary")
        sid = f"s{i + 1:02d}"
        visual_type, chart_type = chapter_visual.get(tag, ("cards", "none"))
        slides.append(
            {
                "id": sid,
                "chapter_id": chapter_to_id.get(tag, "ch1"),
                "chapter_tag": tag,
                "template": sk.get("template", "exec_summary_3proof"),
                "headline": f"[{sid}] Insert conclusion headline with decision impact",
                "key_question": f"[{sid}] What decision should be made and why now?",
                "so_what": "[Insert implication and required decision/action]",
                "logic_chain": {
                    "claim": "[Insert one clear claim]",
                    "because": ["[Evidence 1]", "[Evidence 2]"],
                    "therefore": "[Insert decision/action]",
                },
                "proof_blocks": [
                    {"type": "metric", "label": "Primary KPI", "value": "[value]"},
                    {"type": "benchmark", "label": "External benchmark", "value": "[value]"},
                ],
                "visual_spec": {"visual_type": visual_type, "chart_type": chart_type},
                "design_tokens": {
                    "icon_style": "outline",
                    "icon_keywords": chapter_icon.get(tag, ["insight", "action"]),
                    "visual_tone": "boardroom",
                    "layout_density": "balanced",
                },
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
            "story_quality_target": 90,
            "design_quality_target": 88,
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
        if not _check_proof_density(slide):
            major.append({"code": "QC_011_PROOF_DENSITY_TOO_LOW", "message": f"{sid}: should include at least 2 proof blocks"})
        if not _check_implementation_readiness(slide):
            major.append({"code": "QC_007_IMPLEMENTATION_READINESS_MISSING", "message": f"{sid}: implementation fields missing required details"})
        if not _check_logic_chain(slide):
            major.append({"code": "QC_009_LOGIC_CHAIN_MISSING", "message": f"{sid}: logic_chain (claim-because-therefore) is missing/incomplete"})
        if not _check_icon_plan(slide):
            minor.append({"code": "QC_010_ICON_PLAN_MISSING", "message": f"{sid}: design_tokens.icon_style/icon_keywords should be set"})
        if not _check_visual_richness(slide):
            minor.append({"code": "QC_012_VISUAL_SPEC_TOO_WEAK", "message": f"{sid}: visual_spec.visual_type should not be none"})

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


@tool(description="List built-in industry presets for worldclass deck generation.")
def pptx_list_industry_presets() -> ToolResult:
    items = []
    for k in sorted(_INDUSTRY_PRESETS.keys()):
        p = _INDUSTRY_PRESETS[k]
        items.append(
            {
                "name": k,
                "kpi_label": p.get("kpi_label"),
                "benchmark_label": p.get("benchmark_label"),
                "objectives": p.get("objectives", [])[:2],
            }
        )
    return _json_ok({"presets": items})


@tool(description="List built-in brand style presets for premium deck look and feel.")
def pptx_list_brand_styles() -> ToolResult:
    items = []
    for k in sorted(_BRAND_STYLE_PRESETS.keys()):
        p = _BRAND_STYLE_PRESETS[k]
        items.append(
            {
                "name": k,
                "font_family": p.get("font_family"),
                "headline_size_pt": p.get("headline_size_pt"),
                "icon_style": p.get("icon_style"),
                "layout_density": p.get("layout_density"),
                "visual_tone": p.get("visual_tone"),
            }
        )
    return _json_ok({"brand_styles": items})


@tool(
    description=(
        "Generate a world-class consulting deck spec in one call: "
        "build profile skeleton, inject high-quality defaults, run QC, and return rewrite hints."
    )
)
def pptx_generate_worldclass_deck_spec(
    topic: str,
    audience_level: str = "exec",
    objective_type: str = "decide",
    profile_name: str = "berry_business_strategy",
    page_mode: str = "fixed",
    page_count: int = 5,
    language: str = "zh-CN",
    industry_preset: str = "generic",
    brand_style: str = "consulting_clean",
) -> ToolResult:
    topic = str(topic or "").strip()
    if len(topic) < 4:
        return ToolResult.error("topic is required and should be at least 4 characters.")

    preset = _get_preset(industry_preset)
    brand = _get_brand_style(brand_style)
    base_res = pptx_recommend_slides_by_profile(
        profile_name=profile_name,
        page_mode=page_mode,
        page_count=page_count,
    )
    if base_res.is_error:
        return base_res
    try:
        deck = json.loads(base_res.content)
    except Exception as e:
        return ToolResult.error(f"Failed to parse generated skeleton: {e}")

    deck_meta = deck.get("deck_meta") or {}
    deck_meta["title"] = f"{topic} - 决策方案包"
    deck_meta["audience_level"] = audience_level
    deck_meta["objective_type"] = objective_type
    deck_meta["language"] = language
    deck_meta["story_quality_target"] = max(88, int(deck_meta.get("story_quality_target", 90)))
    deck_meta["design_quality_target"] = max(86, int(deck_meta.get("design_quality_target", 88)))
    deck_meta["theme"] = brand_style
    deck["deck_meta"] = deck_meta

    storyline = deck.get("storyline") or {}
    storyline["core_answer"] = f"围绕“{topic}”，建议采用分阶段推进路径，{preset['core_answer_suffix']}"
    deck["storyline"] = storyline

    slides = deck.get("slides") or []
    for idx, s in enumerate(slides):
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id", f"s{idx+1:02d}"))
        tag = str(s.get("chapter_tag", "summary"))
        action_hint = _chapter_action_hint(tag)
        s["headline"] = f"{topic}在12个月内具备明确收益窗口，建议{action_hint}"
        s["key_question"] = f"{topic}当前最关键的决策问题是什么，为什么必须现在决策？"
        s["so_what"] = f"建议在两周内完成关键资源与责任人确认，以保障{topic}路径按期落地。"
        s["logic_chain"] = {
            "claim": f"{topic}需要优先推进高价值路径",
            "because": [
                "关键指标存在可量化改善空间",
                "窗口期与竞争态势要求快速行动",
            ],
            "therefore": f"应立即明确里程碑、预算与 owner，并进入执行跟踪。",
        }
        s["proof_blocks"] = [
            {"type": "metric", "label": preset["kpi_label"], "value": preset["kpi_value"], "unit": "%", "period": "12个月"},
            {"type": "benchmark", "label": preset["benchmark_label"], "value": preset["benchmark_value"], "unit": "%", "period": "当前"},
        ]
        s["source_refs"] = [
            {"source_id": f"src_{100+idx}", "name": "Internal analysis", "date": "2026-04-01", "confidence": "medium"}
        ]
        if tag == "implementation_roadmap":
            s["implementation"] = {
                "owner": "Program Lead",
                "timeline": "Q2-Q4 2026",
                "requirements": preset["requirements"],
                "success_kpis": [{"name": "核心目标达成率", "target": ">=90%", "by": "2026-12"}],
                "risks": ["资源不足", "跨团队依赖延期"],
                "next_steps": ["确认责任人", "发布里程碑", "启动试点"],
            }
        s["design_tokens"] = {
            "icon_style": brand["icon_style"],
            "icon_keywords": (s.get("design_tokens", {}).get("icon_keywords") if isinstance(s.get("design_tokens"), dict) else None) or ["insight", "action"],
            "visual_tone": brand["visual_tone"],
            "layout_density": brand["layout_density"],
        }

    if any(isinstance(s, dict) and s.get("chapter_tag") == "strategy_house" for s in slides):
        deck["strategy_house"] = {
            "aspiration": f"{topic}实现可持续领先并形成可复制增长引擎",
            "objectives": preset["objectives"],
            "initiatives": preset["initiatives"],
            "enablers": preset["enablers"],
            "foundation": "统一指标体系与经营节奏",
        }
    ext = deck.get("extensions")
    if not isinstance(ext, dict):
        ext = {}
    ext["brand_style"] = {
        "name": brand_style,
        "palette": brand["palette"],
        "font_family": brand["font_family"],
        "headline_size_pt": brand["headline_size_pt"],
        "body_size_pt": brand["body_size_pt"],
        "icon_style": brand["icon_style"],
        "layout_density": brand["layout_density"],
    }
    deck["extensions"] = ext

    qc_res = pptx_run_consulting_qc(json.dumps(deck, ensure_ascii=False))
    if qc_res.is_error:
        return qc_res
    qc = json.loads(qc_res.content)

    return _json_ok(
        {
            "deck_json": deck,
            "qc": qc,
            "industry_preset": industry_preset,
            "brand_style": brand_style,
            "ready_for_render": bool(qc.get("pass", False)),
            "next_action": (
                "Can render directly."
                if qc.get("pass", False)
                else "Revise slides in rewrite_priority_order and rerun QC."
            ),
        }
    )
