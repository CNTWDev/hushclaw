from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_session_history_navigation_uses_stable_bottom_reveal():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")

    assert 'const _messagesBottomSentinel = document.createElement("div");' in chat_js
    assert "function _alignMessagesToBottom()" in chat_js
    assert "els.messages.scrollTop = els.messages.scrollHeight;" in chat_js
    assert 'sentinel.scrollIntoView({ block: "end" });' not in chat_js
    assert "const _HISTORY_BOTTOM_REVEAL_IDLE_MS = 180;" in chat_js
    assert "const _HISTORY_BOTTOM_REVEAL_MAX_MS = 3000;" in chat_js
    assert "function _startHistoryBottomReveal(sessionId)" in chat_js
    assert "new MutationObserver(() => {" in chat_js
    assert "active.mutationObserver.observe(els.messages, { childList: true });" in chat_js
    assert "let _scrollStateRaf = 0;" in chat_js
    assert "function _applyMessagesScrollState()" in chat_js
    assert "requestAnimationFrame(_applyMessagesScrollState);" in chat_js
    assert "if (shouldScrollToLatest) {" in chat_js
    assert "_startHistoryBottomReveal(session_id);" in chat_js
    assert "_cancelHistoryBottomReveal();" in chat_js
    assert "els.messages.addEventListener(\"scroll\", () => {" in chat_js
    assert "} else if (state._aiMsgEl) {" in chat_js


def test_chat_perf_logging_can_be_enabled_for_scroll_diagnostics():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")

    assert 'const _CHAT_PERF_IDLE_MS = 2500;' in chat_js
    assert 'window.__HC_CHAT_PERF = {' in chat_js
    assert 'dump: () => _chatPerf.logs.slice(),' in chat_js
    assert 'const v = (qp.get("hc_chat_perf") || "").trim().toLowerCase();' in chat_js
    assert 'localStorage.getItem("hushclaw.debug.chat_perf")' in chat_js
    assert '_chatPerfPush("longtask",' in chat_js
    assert '_chatPerfPush("scroll-state", { latencyMs, idleMs });' in chat_js
    assert '_chatPerfMarkInput("wheel", { deltaY: Math.round(ev.deltaY || 0) });' in chat_js
    assert '_chatPerfPush("history-bottom-reveal-mutation");' in chat_js
    assert "_initChatPerf();" in chat_js


def test_chat_scroll_styles_use_containment_for_large_histories():
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-theme.css").read_text(encoding="utf-8")

    assert "contain: layout paint;" in chat_css
    assert "@supports (content-visibility: auto) {" in chat_css
    assert "content-visibility: auto;" in chat_css
    assert "contain-intrinsic-size: 0 180px;" in chat_css
    assert "will-change: transform, filter, box-shadow;" not in chat_css


def test_chat_thinking_and_tool_lines_avoid_high_frequency_idle_repaints():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    base_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")

    assert "const updateThinkingText = () => {" in chat_js
    assert "if (sec === lastSec) return;" in chat_js
    assert "state._thinkingTimer = setInterval(updateThinkingText, 1000);" in chat_js
    assert "setInterval(() => {" not in chat_js
    assert ".tool-line {" in base_css
    assert "animation: tl-running 1.8s ease-in-out infinite;" not in base_css
    assert "@keyframes tl-running {" not in base_css


def test_events_boot_marks_connecting_message_without_assuming_last_child():
    events_js = (ROOT / "hushclaw" / "web" / "modules" / "events.js").read_text(encoding="utf-8")

    assert 'insertSystemMsg("Connecting to HushClaw…");' in events_js
    assert 'document.querySelector("#messages .msg:last-of-type")?.setAttribute("id", "msg-connecting");' in events_js
    assert '#messages .msg:last-child' not in events_js


def test_sent_user_messages_can_render_inline_reference_summaries():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    events_js = (ROOT / "hushclaw" / "web" / "modules" / "events.js").read_text(encoding="utf-8")
    refs_js = (ROOT / "hushclaw" / "web" / "modules" / "events" / "references.js").read_text(encoding="utf-8")
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-theme.css").read_text(encoding="utf-8")

    assert "function _renderInlineReferences(container, references = [])" in chat_js
    assert 'item.className = "msg-inline-reference";' in chat_js
    assert 'more.className = "msg-inline-reference msg-inline-reference-more";' in chat_js
    assert "_setBubbleMarkdownContent(bubbleEl, text, { surface: \"chat\", className: \"bubble markdown-body\" }, references);" in chat_js
    assert "const referencePreviewItems = snapshotMessageReferences();" in events_js
    assert "insertUserMsg(displayText, referencePreviewItems);" in events_js
    assert "export function snapshotMessageReferences()" in refs_js
    assert ".msg-inline-references {" in chat_css
    assert ".msg-inline-reference-label {" in chat_css
