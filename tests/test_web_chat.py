from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_session_history_navigation_uses_stable_bottom_reveal():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")

    assert 'const _messagesBottomSentinel = document.createElement("div");' in chat_js
    assert "function _alignMessagesToBottom()" in chat_js
    assert 'sentinel.scrollIntoView({ block: "end" });' in chat_js
    assert "const _HISTORY_BOTTOM_REVEAL_IDLE_MS = 180;" in chat_js
    assert "const _HISTORY_BOTTOM_REVEAL_MAX_MS = 3000;" in chat_js
    assert "function _startHistoryBottomReveal(sessionId)" in chat_js
    assert "new ResizeObserver(() => {" in chat_js
    assert "new MutationObserver(() => {" in chat_js
    assert "if (shouldScrollToLatest) {" in chat_js
    assert "_startHistoryBottomReveal(session_id);" in chat_js
    assert "_cancelHistoryBottomReveal();" in chat_js
    assert "els.messages.addEventListener(\"scroll\", () => {" in chat_js
    assert "} else if (state._aiMsgEl) {" in chat_js
