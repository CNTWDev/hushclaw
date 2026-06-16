from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_session_history_navigation_lands_on_latest_without_saved_scroll_restore():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-theme.css").read_text(encoding="utf-8")

    assert 'const _messagesBottomSentinel = document.createElement("div");' in chat_js
    assert 'function _alignMessagesToBottom(reason = "unknown")' in chat_js
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
    assert "function _chatPerfViewportMetrics()" in chat_js
    assert "function _getLastRenderableNodes()" in chat_js
    assert "function _chatPerfPushViewport(event, extra = {})" in chat_js
    assert "const children = wrap ? Array.from(wrap.children) : [];" in chat_js
    assert '!node.classList.contains("messages-bottom-sentinel")' in chat_js
    assert 'bottomGapPx,' in chat_js
    assert 'bubbleBottomGapPx,' in chat_js
    assert 'lastNodeBottomInScrollPx:' in chat_js
    assert 'lastBubbleViewportBottomPx:' in chat_js
    assert 'export function noteSessionSwitchRequested(sessionId)' in chat_js
    assert 'export function noteSessionHistoryReceived(sessionId, turnCount, { summary = false, lineageCount = 0 } = {})' in chat_js
    assert '_chatPerfPushViewport("session-switch-request",' in chat_js
    assert '_chatPerfPushViewport("session-history-received",' in chat_js
    assert "async function _finalizeHistoryInitialViewport({ keepInProgress = false } = {})" in chat_js
    assert "els.messages.classList.add(\"history-preparing\");" in chat_js
    assert "await _finalizeHistoryInitialViewport({ keepInProgress });" in chat_js
    assert "_alignMessagesToBottom(\"history-initial\");" in chat_js
    assert "_alignMessagesToBottom(\"history-settled\");" in chat_js
    assert "function _alignMessagesToReplyBottom(reason = \"unknown\")" not in chat_js
    assert '_chatPerfPushViewport("session-history-viewport-prep-start");' in chat_js
    assert '_chatPerfPushViewport("session-history-viewport-after-first-sync");' in chat_js
    assert '_chatPerfPushViewport("session-history-viewport-after-second-sync");' in chat_js
    assert '_chatPerfPushViewport("session-history-viewport-final",' in chat_js
    assert 'els.messages.classList.add("history-measuring");' in chat_js
    assert 'els.messages.classList.remove("history-measuring");' in chat_js
    assert 'els.messages.classList.remove("history-preparing");' in chat_js
    assert 'initialViewport: "latest",' in chat_js
    assert "const shouldScrollToLatest = _historyBottomRequests.delete(session_id);" not in chat_js
    assert "const savedTop = _scrollMap.get(session_id);" not in chat_js
    assert "_cancelHistoryBottomReveal();" in chat_js
    assert "els.messages.addEventListener(\"scroll\", () => {" in chat_js
    assert "} else if (state._aiMsgEl) {" in chat_js
    assert "#messages.history-preparing {" in chat_css
    assert "visibility: hidden;" in chat_css
    assert 'if (!useWindowedHistory) {' in chat_js
    assert '_renderSessionSummary(summary);' in chat_js
    assert '_renderSessionLineage(lineage);' in chat_js


def test_chat_perf_logging_is_enabled_by_default_for_scroll_and_render_diagnostics():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")

    assert "const _CHAT_PERF_IDLE_MS = 2500;" in chat_js
    assert "const _CHAT_PERF_MAX_LOGS = 400;" in chat_js
    assert "enabled: true," in chat_js
    assert 'window.__HC_CHAT_PERF = {' in chat_js
    assert 'dump: () => _chatPerf.logs.slice(),' in chat_js
    assert 'clear: () => { _chatPerf.logs.length = 0; }' in chat_js
    assert 'return true;' in chat_js
    assert '_chatPerfPush("scroll-event",' in chat_js
    assert '_chatPerfPush("scroll-state", { latencyMs, idleMs });' in chat_js
    assert '_chatPerfPush("stream-render",' in chat_js
    assert '_chatPerfPush("history-render-start",' in chat_js
    assert '_chatPerfPush("history-render-complete",' in chat_js
    assert 'console.log("[hc-chat-perf]", entry);' in chat_js
    assert 'console.log(`[hc-chat-perf-line] ${summary}`);' in chat_js
    assert 'String(event || "").includes("session-") ||' in chat_js
    assert 'event === "align-bottom"' in chat_js
    assert 'event === "scroll-event"' in chat_js
    assert 'event === "scroll-state"' in chat_js
    assert 'event === "align-reply-bottom"' not in chat_js
    assert '`gap=${entry.bottomGapPx ?? "-"}`' in chat_js
    assert '`bubbleGap=${entry.bubbleBottomGapPx ?? "-"}`' in chat_js
    assert '`maxTop=${entry.maxScrollTop ?? "-"}`' in chat_js
    assert '`bubbleViewportBottom=${entry.lastBubbleViewportBottomPx ?? "-"}`' in chat_js
    assert '`latency=${entry.latencyMs ?? "-"}`' in chat_js
    assert '_chatPerfPush("longtask",' in chat_js
    assert "_initChatPerf();" in chat_js


def test_streaming_markdown_updates_are_time_sliced_instead_of_rendering_every_chunk():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    markdown_js = (ROOT / "hushclaw" / "web" / "modules" / "markdown.js").read_text(encoding="utf-8")

    assert "const _STREAM_RENDER_MIN_MS = 48;" in chat_js
    assert "const _STREAM_RENDER_MIN_CHARS = 160;" in chat_js
    assert "let _streamRenderTimer = 0;" in chat_js
    assert "let _streamBufferedChars = 0;" in chat_js
    assert "let _streamLastRenderTs = 0;" in chat_js
    assert "function _scheduleAiBubbleRender(delayMs = 0)" in chat_js
    assert "function _flushAiBubbleRender()" in chat_js
    assert "_streamBufferedChars += chunkText.length;" in chat_js
    assert "const shouldFlushNow = force || _streamBufferedChars >= _STREAM_RENDER_MIN_CHARS || sinceLastRender >= _STREAM_RENDER_MIN_MS;" in chat_js
    assert "_queueAiBubbleRender(true);" in chat_js
    assert "_flushAiBubbleRender();" in chat_js
    assert "const preferNative = Boolean(options?.preferNative);" in markdown_js
    assert "const api = preferNative ? null : _reactMarkdownApi();" in markdown_js


def test_session_history_static_markdown_prefers_native_renderer_for_stable_height():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")

    assert 'setMarkdownContent(summaryEl, summary, { surface: "chat", preferNative: true });' in chat_js
    assert '{ surface: "chat", className: "bubble markdown-body", preferNative: true }' in chat_js
    assert 'preferNative: true,' in chat_js


def test_large_session_history_uses_tail_first_chunking_instead_of_height_spacers():
    chat_js = (ROOT / "hushclaw" / "web" / "modules" / "chat.js").read_text(encoding="utf-8")
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-theme.css").read_text(encoding="utf-8")

    assert "const _HISTORY_WINDOW_THRESHOLD = 120;" in chat_js
    assert "const _HISTORY_REAL_TAIL_TURNS = 48;" in chat_js
    assert "const _HISTORY_PREPEND_CHUNK_SIZE = 24;" in chat_js
    assert "const _HISTORY_PREPEND_TRIGGER_PX = 220;" in chat_js
    assert "let _historyWindow = null;" in chat_js
    assert "function _scheduleHistoryWindowSync()" in chat_js
    assert "function _prependOlderHistoryChunk()" in chat_js
    assert "function _renderTurnsRange(turns, start, end, parent = els.messages)" in chat_js
    assert 'function _mountWindowedHistory(turns, { summary = "", lineage = [] } = {})' in chat_js
    assert "function _syncHistoryWindow()" in chat_js
    assert "els.messages.scrollTop > _HISTORY_PREPEND_TRIGGER_PX" in chat_js
    assert "const splitIndex = Math.max(0, turns.length - _HISTORY_REAL_TAIL_TURNS);" in chat_js
    assert "_renderTurnsRange(turns, splitIndex, turns.length);" in chat_js
    assert "els.messages.scrollTop = beforeTop + delta;" in chat_js
    assert '_chatPerfPushViewport("history-prepend-complete",' in chat_js
    assert "const useWindowedHistory = !keepInProgress && turnList.length >= _HISTORY_WINDOW_THRESHOLD;" in chat_js
    assert "if (useWindowedHistory) {" in chat_js
    assert ".history-window-block {" in chat_css
    assert ".history-window-spacer {" in chat_css
    assert "#messages.history-measuring .msg," in chat_css
    assert "content-visibility: visible;" in chat_css
    assert "contain-intrinsic-size: auto;" in chat_css
    assert "_estimateTurnHeight" not in chat_js
    assert "_hydrateHistoryTailWindow" not in chat_js
    assert "_dehydrateHistoryBlock" not in chat_js


def test_chat_scroll_styles_use_containment_for_large_histories():
    chat_css = (ROOT / "hushclaw" / "web" / "styles" / "chat-theme.css").read_text(encoding="utf-8")

    assert "contain: layout;" in chat_css
    assert "overflow: visible;" in chat_css
    assert "padding: 26px 30px 0;" in chat_css
    assert "@supports (content-visibility: auto) {" in chat_css
    assert "content-visibility: auto;" in chat_css
    assert "contain-intrinsic-size: 0 180px;" in chat_css
    assert ".tool-line," in chat_css
    assert ".round-line {" in chat_css
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


def test_message_action_footer_defers_button_mount_until_first_interaction():
    export_js = (ROOT / "hushclaw" / "web" / "modules" / "chat" / "export.js").read_text(encoding="utf-8")
    base_css = (ROOT / "hushclaw" / "web" / "style.css").read_text(encoding="utf-8")

    assert "function _buildMessageActionButtons(msgEl, bubbleEl)" in export_js
    assert "function _hydrateMessageActionFooter(footer, msgEl, bubbleEl)" in export_js
    assert 'footer.dataset.actionsHydrated = "0";' in export_js
    assert 'toggleBtn.className = "msg-copy-btn msg-actions-toggle";' in export_js
    assert 'toggleBtn.innerHTML = "⋯ More";' in export_js
    assert 'footer.addEventListener("mouseenter", ensureHydrated, { once: true });' in export_js
    assert 'footer.addEventListener("focusin", ensureHydrated, { once: true });' in export_js
    assert "position: absolute;" in base_css
    assert "top: calc(100% + 2px);" in base_css
    assert "display: inline-flex;" in base_css
    assert "z-index: 6;" in base_css
    assert "padding: 1px 7px;" in base_css
    assert "font-size: 9px;" in base_css
    assert ".msg-actions-host {" in base_css
    assert ".msg-actions-toggle[hidden] {" in base_css


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
