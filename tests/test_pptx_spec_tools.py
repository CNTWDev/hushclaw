from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_spec_module():
    root = Path(__file__).resolve().parents[1]
    mod_path = (
        root
        / "skill-packages"
        / "hushclaw-skill-pptx"
        / "tools"
        / "pptx_spec_tools.py"
    )
    spec = importlib.util.spec_from_file_location("pptx_spec_tools", mod_path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _valid_deck() -> dict:
    return {
        "deck_meta": {
            "title": "Q3 Growth Decision Pack",
            "language": "zh-CN",
            "audience_level": "exec",
            "objective_type": "decide",
            "page_mode": "fixed",
            "page_count": 3,
            "theme": "consulting_clean",
        },
        "storyline": {
            "core_answer": "优先执行方案B以提升利润率并控制风险。",
            "narrative_framework": "scqa",
            "chapters": [{"id": "ch1", "name": "结论", "intent": "形成决策"}],
        },
        "strategy_house": {
            "aspiration": "Become category leader in profitable growth.",
            "objectives": ["Grow core", "Improve margin"],
            "initiatives": ["Initiative A", "Initiative B"],
            "enablers": ["Data platform", "Capability build"],
            "foundation": "People and governance model",
        },
        "slides": [
            {
                "id": "s01",
                "chapter_id": "ch1",
                "chapter_tag": "summary",
                "template": "exec_summary_3proof",
                "headline": "优先推进方案B，可在6个月内提升利润率2.1pp",
                "so_what": "本周需完成预算批准并确定试点区域",
                "proof_blocks": [{"type": "metric", "label": "ROI", "value": "1.8x"}],
                "visual_spec": {"visual_type": "chart", "chart_type": "bar"},
                "source_refs": [
                    {"source_id": "src_001", "name": "FinanceModel", "date": "2026-04-01", "confidence": "high"}
                ],
                "qc": {"must_pass": ["answer_first", "source_required"]},
            },
            {
                "id": "s02",
                "chapter_id": "ch1",
                "chapter_tag": "strategy_house",
                "template": "strategy_house_overview",
                "headline": "方案B在收益与实施复杂度上最优",
                "so_what": "A保留备选，C暂缓",
                "proof_blocks": [{"type": "benchmark", "label": "回收期", "value": "7个月"}],
                "visual_spec": {"visual_type": "table", "chart_type": "none"},
                "source_refs": [
                    {"source_id": "src_002", "name": "PilotData", "date": "2026-04-01", "confidence": "medium"}
                ],
                "qc": {"must_pass": ["answer_first"]},
            },
            {
                "id": "s03",
                "chapter_id": "ch1",
                "chapter_tag": "implementation_roadmap",
                "template": "decision_next_steps",
                "headline": "请在48小时内确认资源与里程碑",
                "so_what": "若延期，Q3窗口将缩短",
                "proof_blocks": [{"type": "case", "label": "历史项目", "value": "延迟导致收益损失"}],
                "visual_spec": {"visual_type": "process", "chart_type": "none"},
                "implementation": {
                    "owner": "PMO Lead",
                    "timeline": "Q3-Q4 2026",
                    "success_kpis": [{"name": "Margin uplift", "target": "2.1pp", "by": "2026-12"}],
                    "next_steps": ["Approve budget", "Launch pilot"]
                },
                "qc": {"must_pass": ["actionability_required"]},
            },
        ],
    }


def test_get_schema():
    mod = _load_spec_module()
    out = mod.pptx_get_deck_schema()
    assert not out.is_error
    payload = json.loads(out.content)
    assert payload["title"] == "ConsultingDeckSpec"


def test_validate_deck_spec_valid():
    mod = _load_spec_module()
    deck = json.dumps(_valid_deck(), ensure_ascii=False)
    out = mod.pptx_validate_deck_spec(deck)
    assert not out.is_error
    payload = json.loads(out.content)
    assert payload["valid"] is True


def test_validate_deck_spec_fixed_requires_page_count():
    mod = _load_spec_module()
    deck = _valid_deck()
    del deck["deck_meta"]["page_count"]
    out = mod.pptx_validate_deck_spec(json.dumps(deck, ensure_ascii=False))
    payload = json.loads(out.content)
    assert payload["valid"] is False
    codes = [x["code"] for x in payload["issues"]]
    assert "QC_201_PAGE_MODE_FIXED_MISSING_COUNT" in codes


def test_list_story_profiles_contains_berry():
    mod = _load_spec_module()
    out = mod.pptx_list_story_profiles()
    payload = json.loads(out.content)
    names = [x["name"] for x in payload["profiles"]]
    assert "berry_business_strategy" in names


def test_recommend_slides_by_profile_fixed_5():
    mod = _load_spec_module()
    out = mod.pptx_recommend_slides_by_profile("berry_business_strategy", "fixed", 5)
    payload = json.loads(out.content)
    assert payload["deck_meta"]["page_count"] == 5
    assert len(payload["slides"]) == 5
    assert payload["slides"][0]["chapter_tag"] == "summary"


def test_qc_detects_missing_source_for_numeric_claim():
    mod = _load_spec_module()
    deck = _valid_deck()
    deck["slides"][0]["source_refs"] = []
    out = mod.pptx_run_consulting_qc(json.dumps(deck, ensure_ascii=False))
    payload = json.loads(out.content)
    codes = [x["code"] for x in payload["fatal_issues"]]
    assert "QC_003_SOURCE_REQUIRED" in codes
    assert payload["pass"] is False


def test_qc_detects_chapter_order_violation():
    mod = _load_spec_module()
    deck = _valid_deck()
    # Put implementation before strategy to break sequence.
    deck["slides"][1]["chapter_tag"] = "implementation_roadmap"
    deck["slides"][2]["chapter_tag"] = "strategy_house"
    out = mod.pptx_run_consulting_qc(json.dumps(deck, ensure_ascii=False))
    payload = json.loads(out.content)
    codes = [x["code"] for x in payload["major_issues"]]
    assert "QC_006_CHAPTER_SEQUENCE_INVALID" in codes


def test_qc_detects_strategy_house_incomplete():
    mod = _load_spec_module()
    deck = _valid_deck()
    del deck["strategy_house"]["foundation"]
    out = mod.pptx_run_consulting_qc(json.dumps(deck, ensure_ascii=False))
    payload = json.loads(out.content)
    codes = [x["code"] for x in payload["major_issues"]]
    assert "QC_008_STRATEGY_HOUSE_INCOMPLETE" in codes


def test_qc_detects_implementation_readiness_missing():
    mod = _load_spec_module()
    deck = _valid_deck()
    deck["slides"][2]["implementation"] = {"owner": "PMO"}
    out = mod.pptx_run_consulting_qc(json.dumps(deck, ensure_ascii=False))
    payload = json.loads(out.content)
    codes = [x["code"] for x in payload["major_issues"]]
    assert "QC_007_IMPLEMENTATION_READINESS_MISSING" in codes
