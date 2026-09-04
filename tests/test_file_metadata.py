from hushclaw.runtime.file_metadata import normalize_file_tags, suggest_file_tags


def test_normalize_file_tags_deduplicates_and_limits_invalid_values():
    tags = normalize_file_tags([" 战略 ", "战略", "#市场", "", "x" * 40])

    assert tags == [("战略", "战略"), ("市场", "市场")]


def test_suggest_file_tags_uses_format_and_filename_without_model_call():
    assert suggest_file_tags("传音_AI市场战略分析报告.md") == ["文档", "战略", "市场", "研究", "报告"]
    assert suggest_file_tags("quarterly-budget.xlsx") == ["表格", "财务"]
