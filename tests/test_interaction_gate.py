from __future__ import annotations

from hushclaw.providers.base import LLMResponse, ToolCall
from hushclaw.runtime.interaction import InteractionGate


def test_asks_for_input_detects_chinese_confirmation_questions():
    assert InteractionGate.asks_for_input("明白了吗？我现在按这个逻辑构建 skill，你确认吗？")
    assert InteractionGate.asks_for_input("这个方向可以吗？有什么想补充的方向？")


def test_asks_for_input_detects_english_confirmation_questions():
    assert InteractionGate.asks_for_input("Please confirm before I continue.")
    assert InteractionGate.asks_for_input("Does that look right? Anything to add?")


def test_asks_for_input_ignores_empty_or_plain_text():
    assert not InteractionGate.asks_for_input("")
    assert not InteractionGate.asks_for_input("I will summarize the result now.")


def test_should_pause_before_tools_requires_tool_use_with_tools():
    response = LLMResponse(
        content="你确认吗？",
        stop_reason="tool_use",
        tool_calls=[ToolCall(id="tc-1", name="remember_skill", input={"name": "x"})],
    )
    assert InteractionGate.should_pause_before_tools(response)

    response_without_tools = LLMResponse(content="你确认吗？", stop_reason="tool_use", tool_calls=[])
    assert not InteractionGate.should_pause_before_tools(response_without_tools)

    end_turn_response = LLMResponse(
        content="你确认吗？",
        stop_reason="end_turn",
        tool_calls=[ToolCall(id="tc-1", name="remember_skill", input={"name": "x"})],
    )
    assert not InteractionGate.should_pause_before_tools(end_turn_response)


def test_should_pause_before_tools_uses_visible_streamed_text():
    response = LLMResponse(
        content="",
        stop_reason="tool_use",
        tool_calls=[ToolCall(id="tc-1", name="remember_skill", input={"name": "x"})],
    )
    assert InteractionGate.should_pause_before_tools(response, "请确认后我再继续。")


def test_is_plain_confirmation_accepts_simple_confirmations():
    for text in ("确认", "可以", "继续", "ok", "go ahead", "confirmed"):
        assert InteractionGate.is_plain_confirmation(text)


def test_is_plain_confirmation_rejects_changes_or_corrections():
    for text in ("可以，但是先别执行", "确认，不过改成英文", "ok but change the name", "do not continue"):
        assert not InteractionGate.is_plain_confirmation(text)
