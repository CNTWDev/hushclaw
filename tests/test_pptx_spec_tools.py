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
                "key_question": "我们应该优先选择哪条方案实现利润率提升？",
                "so_what": "本周需完成预算批准并确定试点区域",
                "logic_chain": {
                    "claim": "优先推进方案B",
                    "because": ["ROI最高", "回收期最短"],
                    "therefore": "立即审批预算并启动试点",
                },
                "proof_blocks": [
                    {"type": "metric", "label": "ROI", "value": "1.8x"},
                    {"type": "benchmark", "label": "对标中位数", "value": "1.2x"},
                ],
                "visual_spec": {"visual_type": "chart", "chart_type": "bar"},
                "design_tokens": {
                    "icon_style": "outline",
                    "icon_keywords": ["decision", "kpi", "growth"],
                    "visual_tone": "boardroom",
                    "layout_density": "balanced",
                },
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
                "key_question": "为什么方案B优于A与C？",
                "so_what": "A保留备选，C暂缓",
                "logic_chain": {
                    "claim": "方案B为最优路径",
                    "because": ["收益更高", "复杂度可控"],
                    "therefore": "将B作为主路径推进",
                },
                "proof_blocks": [
                    {"type": "benchmark", "label": "回收期", "value": "7个月"},
                    {"type": "metric", "label": "利润率增幅", "value": "2.1pp"},
                ],
                "visual_spec": {"visual_type": "table", "chart_type": "none"},
                "design_tokens": {
                    "icon_style": "outline",
                    "icon_keywords": ["pillar", "tradeoff", "strategy"],
                    "visual_tone": "boardroom",
                    "layout_density": "balanced",
                },
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
                "key_question": "如何确保方案B按期落地？",
                "so_what": "若延期，Q3窗口将缩短",
                "logic_chain": {
                    "claim": "必须在48小时内完成关键决策",
                    "because": ["窗口期有限", "延迟会吞噬收益"],
                    "therefore": "立刻确认资源与里程碑",
                },
                "proof_blocks": [
                    {"type": "case", "label": "历史项目", "value": "延迟导致收益损失"},
                    {"type": "metric", "label": "窗口损失", "value": "0.6pp"},
                ],
                "visual_spec": {"visual_type": "process", "chart_type": "none"},
                "design_tokens": {
                    "icon_style": "outline",
                    "icon_keywords": ["timeline", "milestone", "execution"],
                    "visual_tone": "boardroom",
                    "layout_density": "balanced",
                },
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
    assert "logic_chain" in payload["slides"][0]
    assert "design_tokens" in payload["slides"][0]


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


def test_qc_detects_logic_chain_missing():
    mod = _load_spec_module()
    deck = _valid_deck()
    del deck["slides"][1]["logic_chain"]
    out = mod.pptx_run_consulting_qc(json.dumps(deck, ensure_ascii=False))
    payload = json.loads(out.content)
    codes = [x["code"] for x in payload["major_issues"]]
    assert "QC_009_LOGIC_CHAIN_MISSING" in codes


def test_qc_detects_icon_plan_missing():
    mod = _load_spec_module()
    deck = _valid_deck()
    del deck["slides"][1]["design_tokens"]
    out = mod.pptx_run_consulting_qc(json.dumps(deck, ensure_ascii=False))
    payload = json.loads(out.content)
    codes = [x["code"] for x in payload["minor_issues"]]
    assert "QC_010_ICON_PLAN_MISSING" in codes


def test_generate_worldclass_deck_spec_passes_qc():
    mod = _load_spec_module()
    out = mod.pptx_generate_worldclass_deck_spec(topic="跨境电商增长战略", page_count=5)
    assert not out.is_error
    payload = json.loads(out.content)
    assert "deck_json" in payload
    assert "qc" in payload
    assert payload["qc"]["score_total"] >= 85
    assert payload["ready_for_render"] is True


def test_list_industry_presets_contains_cross_border():
    mod = _load_spec_module()
    out = mod.pptx_list_industry_presets()
    assert not out.is_error
    payload = json.loads(out.content)
    names = [x["name"] for x in payload["presets"]]
    assert "cross_border_ecommerce" in names


def test_worldclass_deck_uses_industry_preset_labels():
    mod = _load_spec_module()
    out = mod.pptx_generate_worldclass_deck_spec(
        topic="全球化DTC增长",
        page_count=5,
        industry_preset="cross_border_ecommerce",
    )
    payload = json.loads(out.content)
    first_slide = payload["deck_json"]["slides"][0]
    labels = [x["label"] for x in first_slide["proof_blocks"]]
    assert "GMV增长率" in labels


def test_list_brand_styles_contains_mckinsey_like():
    mod = _load_spec_module()
    out = mod.pptx_list_brand_styles()
    assert not out.is_error
    payload = json.loads(out.content)
    names = [x["name"] for x in payload["brand_styles"]]
    assert "mckinsey_like" in names


def test_worldclass_deck_uses_brand_style_theme():
    mod = _load_spec_module()
    out = mod.pptx_generate_worldclass_deck_spec(
        topic="企业AI转型路线图",
        page_count=5,
        brand_style="mckinsey_like",
    )
    payload = json.loads(out.content)
    deck = payload["deck_json"]
    assert deck["deck_meta"]["theme"] == "mckinsey_like"
    assert deck["extensions"]["brand_style"]["name"] == "mckinsey_like"
