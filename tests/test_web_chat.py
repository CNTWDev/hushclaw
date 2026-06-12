from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_session_history_navigation_uses_stable_bottom_reveal():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")

    assert "const _HISTORY_BOTTOM_REVEAL_WINDOW_MS = 1200;" in chat_js
    assert "const _HISTORY_BOTTOM_REVEAL_STABLE_FRAMES = 4;" in chat_js
    assert "function _startHistoryBottomReveal(sessionId)" in chat_js
    assert "new ResizeObserver(() => {" in chat_js
    assert "if (shouldScrollToLatest) {" in chat_js
    assert "_startHistoryBottomReveal(session_id);" in chat_js
    assert "_cancelHistoryBottomReveal();" in chat_js
    assert "if (active.sessionId !== getCurrentSessionId()) {" in chat_js
