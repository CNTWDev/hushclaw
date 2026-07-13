"""Tests for deterministic per-turn execution strategy."""
from __future__ import annotations


from hushclaw.runtime.strategy import classify_task


def test_classifier_fallback_does_not_guess_from_language():
    strategy = classify_task("你好")

    assert strategy.intent == "general"
    assert strategy.requires_tools is True
    assert strategy.allowed_tools is None


def test_classifier_fallback_keeps_tools_for_short_continuations():
    strategy = classify_task("继续啊。")

    assert strategy.intent == "general"
    assert strategy.requires_tools is True
    assert strategy.allowed_tools is None


def test_classifier_fallback_keeps_tools_for_research():
    strategy = classify_task("帮我查一下最新的 Python 3.13 变化")

    assert strategy.intent == "general"
    assert strategy.requires_tools is True


def test_classifier_fallback_is_conservative_for_code():
    strategy = classify_task("修复这个 pytest 报错并运行测试")

    assert strategy.requires_tools is True


def test_classifier_fallback_is_conservative_for_external_actions():
    strategy = classify_task("发布这条消息")

    assert strategy.requires_tools is True


def test_classifier_fallback_does_not_disable_tools_for_skill_work():
    strategy = classify_task("先讨论这个 skill 逻辑")

    assert strategy.allowed_tools is None


def test_classifier_fallback_uses_safe_reflection_taxonomy():
    assert classify_task("查一下最新的 Python 变化").reflection_fingerprint() == "general_assistance"
    assert classify_task("修复这个 pytest 报错").reflection_fingerprint() == "general_assistance"
