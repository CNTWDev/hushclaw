"""Tests for deterministic per-turn execution strategy."""
from __future__ import annotations


from hushclaw.runtime.strategy import classify_task


def test_short_conversation_hides_tools_and_uses_cheap_tier():
    strategy = classify_task("你好")

    assert strategy.intent == "conversation"
    assert strategy.max_tool_rounds == 0
    assert strategy.allowed_tools == frozenset()


def test_short_continuation_keeps_tools_for_the_previous_task():
    strategy = classify_task("继续啊。")

    assert strategy.intent == "continuation"
    assert strategy.max_tool_rounds == 8
    assert strategy.allowed_tools is None


def test_research_turn_gets_bounded_research_strategy():
    strategy = classify_task("帮我查一下最新的 Python 3.13 变化")

    assert strategy.intent == "research"
    assert strategy.max_tool_rounds == 8


def test_code_turn_requires_verification():
    strategy = classify_task("修复这个 pytest 报错并运行测试")

    assert strategy.intent == "code_change"


def test_external_side_effect_has_strict_bounded_rounds():
    strategy = classify_task("发布这条消息")

    assert strategy.intent == "external_side_effect"
    assert strategy.max_tool_rounds == 6


def test_skill_operation_is_not_misclassified_as_chat():
    strategy = classify_task("先讨论这个 skill 逻辑")

    assert strategy.intent == "general"
    assert strategy.allowed_tools is None


def test_strategy_reuses_existing_reflection_taxonomy():
    assert classify_task("查一下最新的 Python 变化").reflection_fingerprint() == "web_research"
    assert classify_task("修复这个 pytest 报错").reflection_fingerprint() == "code_change"
