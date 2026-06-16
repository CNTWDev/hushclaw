/**
 * chat.js — Chat message rendering, thinking indicator, session history.
 *
 * Tool-related rendering lives in chat/tools.js.
 * Copy/export/share actions live in chat/export.js.
 */

import {
  state, els, SPINNERS, escHtml,
  isSessionRunning, setCurrentSessionId, clearCurrentSessionId, getCurrentSessionId, debugUiLifecycle,
} from "./state.js";
import { setMarkdownContent } from "./markdown.js";
import { refreshChatStats } from "./stats.js";

import {
  resetActiveRound, finalizeActiveRound, renderToolResult,
} from "./chat/tools.js";
import { addCopyActions } from "./chat/export.js";

// Re-export everything consumers need from the submodules, keeping the public
// surface of chat.js unchanged.
export {
  isDevMode, setDevMode,
  insertToolBubble, updateToolBubble, renderToolResult,
  finalizeActiveRound, createToolRound, insertRoundLine,
} from "./chat/tools.js";

export {
  addCopyActions, exportCurrentSessionAsPdf,
} from "./chat/export.js";

let _spinIdx = 0;
let _streamRenderQueued = false;
let _streamCaretHideTimer = null;

function _turnDate(t) {
  const raw = Number(t?.ts || 0);
  if (!Number.isFinite(raw) || raw <= 0) return new Date();
  // Legacy turns use epoch seconds; event-log replay uses epoch milliseconds.
  const ms = raw < 1_000_000_000_000 ? raw * 1000 : raw;
  const d = new Date(ms);
  return Number.isNaN(d.getTime()) ? new Date() : d;
}
let _lastMarkdownRenderTs = 0;

function _clearStreamTimers() {
  if (_streamCaretHideTimer) {
    clearTimeout(_streamCaretHideTimer);
    _streamCaretHideTimer = null;
  }
}

function _removeStreamCaret(bubbleEl) {
  bubbleEl?.querySelector(".stream-caret")?.remove();
}

function _setAiStreamingState(active) {
  const msgEl = state._aiMsgEl;
  const bubbleEl = state._aiBubbleEl;
  if (!msgEl || !bubbleEl) return;
  msgEl.classList.toggle("msg-streaming", active);
  bubbleEl.classList.toggle("bubble-streaming", active);
  if (!active) {
    bubbleEl.classList.remove("bubble-caret-visible");
    _removeStreamCaret(bubbleEl);
  }
}

function _ensureStreamCaret(bubbleEl) {
  let caret = bubbleEl.querySelector(".stream-caret");
  if (!caret) {
    caret = document.createElement("span");
    caret.className = "stream-caret";
    caret.setAttribute("aria-hidden", "true");
    bubbleEl.appendChild(caret);
  } else if (bubbleEl.lastElementChild !== caret) {
    bubbleEl.appendChild(caret);
  }
  return caret;
}

function _animateStreamChunk(bubbleEl) {
  if (!bubbleEl) return;
  bubbleEl.classList.add("bubble-caret-visible");
  _clearStreamTimers();
  _streamCaretHideTimer = setTimeout(() => {
    bubbleEl.classList.remove("bubble-caret-visible");
    _streamCaretHideTimer = null;
  }, 1200);
}

function _renderAiBubbleNow() {
  _streamRenderQueued = false;
  const bubbleEl = state._aiBubbleEl;
  if (!bubbleEl) return;

  const raw = bubbleEl._raw || "";
  setMarkdownContent(bubbleEl, raw, { surface: "chat", streaming: true, className: "bubble markdown-body" });
  _lastMarkdownRenderTs = Date.now();
  _ensureStreamCaret(bubbleEl);
  _animateStreamChunk(bubbleEl);
  _scrollToBottomIfAuto();
}

function _queueAiBubbleRender() {
  if (_streamRenderQueued) return;
  _streamRenderQueued = true;
  requestAnimationFrame(_renderAiBubbleNow);
}

function _finishAiMessageNow() {
  if (_streamRenderQueued && state._aiBubbleEl) {
    _renderAiBubbleNow();
  }
  removeThinkingMsg();
  finalizeActiveRound();
  if (state._aiMsgEl && !state._aiBubbleEl?._raw?.trim()) {
    state._aiMsgEl.remove();
  }
  if (state._aiBubbleEl) {
    _clearStreamTimers();
    _setAiStreamingState(false);
  }
  state._aiMsgEl = null;
  state._aiBubbleEl = null;
  _streamRenderQueued = false;
  _lastMarkdownRenderTs = 0;
  _autoScroll = true;
  _updateJumpBtn();
}

// Show / hide all share-forum buttons when auth state changes.
document.addEventListener("hc:forum-ready", () => {
  document.querySelectorAll(".share-forum-btn").forEach(b => { b.style.display = ""; });
});
document.addEventListener("hc:forum-unauthed", () => {
  document.querySelectorAll(".share-forum-btn").forEach(b => { b.style.display = "none"; });
});

// ── Smart auto-scroll ──────────────────────────────────────────────────────
let _autoScroll = true;        // false = user scrolled up during streaming
const _SCROLL_THRESHOLD = 80;  // px from bottom to count as "at bottom"
const _scrollMap = new Map();  // sessionId → saved scrollTop
const _historyBottomRequests = new Set(); // explicit session navigation → latest message
const _HISTORY_BOTTOM_REVEAL_IDLE_MS = 180;
const _HISTORY_BOTTOM_REVEAL_MAX_MS = 3000;
let _lastTouchY = 0;
let _historyBottomReveal = null;
let _scrollStateRaf = 0;
const _messagesBottomSentinel = document.createElement("div");
_messagesBottomSentinel.className = "messages-bottom-sentinel";
_messagesBottomSentinel.setAttribute("aria-hidden", "true");
_messagesBottomSentinel.style.cssText = "width:100%;height:1px;pointer-events:none;";

export function saveScrollPosition(sessionId) {
  if (sessionId && els.messages) _scrollMap.set(sessionId, els.messages.scrollTop);
}

function _isNearBottom() {
  const el = els.messages;
  return el.scrollHeight - el.scrollTop - el.clientHeight < _SCROLL_THRESHOLD;
}

function _updateJumpBtn() {
  const btn = document.getElementById("scroll-jump-btn");
  if (!btn) return;
  btn.hidden = _autoScroll || !state._aiMsgEl;
}

function _scrollToBottomIfAuto() {
  if (_autoScroll) _alignMessagesToBottom();
  _updateJumpBtn();
}

function _cancelHistoryBottomReveal() {
  const active = _historyBottomReveal;
  if (!active) return;
  _historyBottomReveal = null;
  if (active.raf) cancelAnimationFrame(active.raf);
  if (active.idleTimer) clearTimeout(active.idleTimer);
  if (active.mutationObserver) active.mutationObserver.disconnect();
}

function _ensureMessagesBottomSentinel() {
  if (!els.messages) return null;
  if (_messagesBottomSentinel.parentElement !== els.messages || els.messages.lastElementChild !== _messagesBottomSentinel) {
    els.messages.appendChild(_messagesBottomSentinel);
  }
  return _messagesBottomSentinel;
}

function _alignMessagesToBottom() {
  _ensureMessagesBottomSentinel();
  els.messages.scrollTop = els.messages.scrollHeight;
}

function _scheduleHistoryBottomRevealSettle(active) {
  if (!active || _historyBottomReveal !== active) return;
  if (active.idleTimer) clearTimeout(active.idleTimer);
  active.idleTimer = setTimeout(() => {
    if (_historyBottomReveal !== active) return;
    _alignMessagesToBottom();
    _cancelHistoryBottomReveal();
  }, _HISTORY_BOTTOM_REVEAL_IDLE_MS);
}

function _scheduleHistoryBottomAlign(active) {
  if (!active || _historyBottomReveal !== active || active.raf) return;
  active.raf = requestAnimationFrame(() => {
    active.raf = 0;
    if (_historyBottomReveal !== active) return;
    if (active.sessionId !== getCurrentSessionId()) {
      _cancelHistoryBottomReveal();
      return;
    }
    _alignMessagesToBottom();
    _scheduleHistoryBottomRevealSettle(active);
    if (performance.now() >= active.deadline) {
      _cancelHistoryBottomReveal();
      return;
    }
  });
}

function _startHistoryBottomReveal(sessionId) {
  if (!sessionId || !els.messages) return;
  _cancelHistoryBottomReveal();
  _autoScroll = true;
  _updateJumpBtn();
  const active = {
    sessionId,
    deadline: performance.now() + _HISTORY_BOTTOM_REVEAL_MAX_MS,
    raf: 0,
    idleTimer: 0,
    mutationObserver: null,
  };
  _historyBottomReveal = active;
  if (typeof MutationObserver === "function") {
    active.mutationObserver = new MutationObserver(() => {
      if (_historyBottomReveal !== active) return;
      _scheduleHistoryBottomAlign(active);
    });
    active.mutationObserver.observe(els.messages, { childList: true });
  }
  _scheduleHistoryBottomAlign(active);
}

function _applyMessagesScrollState() {
  _scrollStateRaf = 0;
  if (_isNearBottom()) {
    _autoScroll = true;
  } else if (state._aiMsgEl) {
    _autoScroll = false;  // pause only while streaming
  }
  _updateJumpBtn();
}

function _pauseAutoScrollForUserIntent() {
  if (!state._aiMsgEl) return;
  if (_isNearBottom()) return;
  _autoScroll = false;
  _updateJumpBtn();
}

els.messages.addEventListener("wheel", (ev) => {
  if (ev.deltaY < 0) {
    _cancelHistoryBottomReveal();
    _autoScroll = false;
    _updateJumpBtn();
  }
}, { passive: true });

els.messages.addEventListener("touchstart", (ev) => {
  _lastTouchY = ev.touches?.[0]?.clientY || 0;
}, { passive: true });

els.messages.addEventListener("touchmove", (ev) => {
  const y = ev.touches?.[0]?.clientY || 0;
  if (y > _lastTouchY) {
    _cancelHistoryBottomReveal();
    _autoScroll = false;
    _updateJumpBtn();
  } else {
    _pauseAutoScrollForUserIntent();
  }
  _lastTouchY = y;
}, { passive: true });

els.messages.addEventListener("keydown", (ev) => {
  if (!["ArrowUp", "PageUp", "Home"].includes(ev.key) && !(ev.key === " " && ev.shiftKey)) return;
  _cancelHistoryBottomReveal();
  _pauseAutoScrollForUserIntent();
});

els.messages.addEventListener("scroll", () => {
  if (_scrollStateRaf) return;
  _scrollStateRaf = requestAnimationFrame(_applyMessagesScrollState);
}, { passive: true });

// Jump button — anchored to #chat-area (position: relative)
(() => {
  const btn = document.createElement("button");
  btn.id = "scroll-jump-btn";
  btn.hidden = true;
  btn.textContent = "↓ Jump to bottom";
  btn.addEventListener("click", () => { _autoScroll = true; scrollToBottom(); });
  els.chatArea.appendChild(btn);
})();
// ──────────────────────────────────────────────────────────────────────────

// ── Scrolling ──────────────────────────────────────────────────────────────

export function scrollToBottom() {
  _cancelHistoryBottomReveal();
  _autoScroll = true;
  _alignMessagesToBottom();
  _updateJumpBtn();
}

export function requestSessionHistoryBottom(sessionId) {
  if (sessionId) _historyBottomRequests.add(sessionId);
}

// ── Message bubble factory ─────────────────────────────────────────────────

export function createMsgBubble(kind) {
  const msgEl = document.createElement("div");
  msgEl.className = `msg ${kind}`;
  msgEl.dataset.role = kind;

  const innerEl = document.createElement("div");
  innerEl.className = "msg-inner";

  const avatarEl = document.createElement("span");
  avatarEl.className = "msg-avatar";
  if (kind === "user") avatarEl.textContent = "You";
  else if (kind === "ai") avatarEl.textContent = "AI";
  else if (kind === "system") avatarEl.textContent = "SYS";
  else avatarEl.textContent = "!";

  const contentEl = document.createElement("div");
  contentEl.className = "msg-content";

  const metaEl = document.createElement("div");
  metaEl.className = "msg-meta";
  if (kind === "user") metaEl.textContent = "You";
  else if (kind === "ai") metaEl.textContent = "Assistant";
  else if (kind === "system") metaEl.textContent = "System";
  else metaEl.textContent = "Error";

  const bubbleEl = document.createElement("div");
  bubbleEl.className = "bubble";
  contentEl.appendChild(metaEl);
  contentEl.appendChild(bubbleEl);
  innerEl.appendChild(avatarEl);
  innerEl.appendChild(contentEl);
  msgEl.appendChild(innerEl);
  return { msgEl, bubbleEl, metaEl, contentEl };
}

function _normalizeTurnReferences(references = []) {
  if (!Array.isArray(references)) return [];
  return references
    .map((ref) => {
      if (!ref || typeof ref !== "object") return null;
      const messageId = String(ref.message_id || "").trim();
      const role = String(ref.role || "").trim();
      const preview = String(ref.preview || "").replace(/\s+/g, " ").trim();
      if (!messageId && !preview) return null;
      return { message_id: messageId, role, preview };
    })
    .filter(Boolean)
    .slice(0, 5);
}

function _renderInlineReferences(container, references = []) {
  const refs = _normalizeTurnReferences(references);
  container.querySelector(".msg-inline-references")?.remove();
  if (!refs.length) return;
  const wrap = document.createElement("div");
  wrap.className = "msg-inline-references";
  const visible = refs.slice(0, 2);
  for (const ref of visible) {
    const item = document.createElement("div");
    item.className = "msg-inline-reference";
    const role = ref.role ? `${ref.role}: ` : "";
    item.innerHTML = `
      <span class="msg-inline-reference-label">引用</span>
      <span class="msg-inline-reference-text">${escHtml(`${role}${ref.preview || ref.message_id}`)}</span>
    `;
    wrap.appendChild(item);
  }
  if (refs.length > visible.length) {
    const more = document.createElement("div");
    more.className = "msg-inline-reference msg-inline-reference-more";
    more.textContent = `+${refs.length - visible.length}`;
    wrap.appendChild(more);
  }
  container.prepend(wrap);
}

function _setBubbleMarkdownContent(bubbleEl, raw, options = {}, references = []) {
  bubbleEl._raw = String(raw || "");
  const refs = _normalizeTurnReferences(references);
  if (!refs.length) {
    setMarkdownContent(bubbleEl, bubbleEl._raw, options);
    return;
  }
  bubbleEl.className = Array.from(new Set(`${bubbleEl.className || ""} bubble-has-references`.trim().split(/\s+/).filter(Boolean))).join(" ");
  bubbleEl.innerHTML = "";
  _renderInlineReferences(bubbleEl, refs);
  const bodyEl = document.createElement("div");
  bodyEl.className = "msg-inline-reference-body";
  bubbleEl.appendChild(bodyEl);
  setMarkdownContent(bodyEl, bubbleEl._raw, { ...options, className: "markdown-body msg-inline-reference-body" });
}

// ── Chat message helpers ───────────────────────────────────────────────────

export function insertUserMsg(text, references = []) {
  const { msgEl, bubbleEl, contentEl } = createMsgBubble("user");
  _setBubbleMarkdownContent(bubbleEl, text, { surface: "chat", className: "bubble markdown-body" }, references);
  addCopyActions(msgEl, bubbleEl, contentEl, new Date());
  els.messages.appendChild(msgEl);
  _ensureMessagesBottomSentinel();
  state._lastUserMsgEl = msgEl;
  refreshChatStats();
  scrollToBottom();
}

function _refreshMessageActions(msgEl) {
  if (!msgEl) return;
  const bubbleEl = msgEl.querySelector(".bubble");
  const contentEl = msgEl.querySelector(".msg-content");
  if (!bubbleEl || !contentEl) return;
  contentEl.querySelector(".msg-actions-footer")?.remove();
  addCopyActions(msgEl, bubbleEl, contentEl, new Date());
}

export function applyLiveMessageIds({ userMessageId = "", assistantMessageId = "" } = {}) {
  if (userMessageId && state._lastUserMsgEl) {
    state._lastUserMsgEl.dataset.messageId = userMessageId;
    _refreshMessageActions(state._lastUserMsgEl);
  }
  if (assistantMessageId && state._aiMsgEl) {
    state._aiMsgEl.dataset.messageId = assistantMessageId;
    _refreshMessageActions(state._aiMsgEl);
  }
}

export function insertSystemMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("system");
  bubbleEl.textContent = text;
  els.messages.appendChild(msgEl);
  _ensureMessagesBottomSentinel();
  scrollToBottom();
}

export function insertErrorMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("error");
  bubbleEl.textContent = "Error: " + text;
  els.messages.appendChild(msgEl);
  _ensureMessagesBottomSentinel();
  scrollToBottom();
}

// ── Streaming AI response ──────────────────────────────────────────────────

export function appendChunk(text) {
  if (!state._aiMsgEl) {
    const { msgEl, bubbleEl, contentEl } = createMsgBubble("ai");
    state._aiMsgEl    = msgEl;
    state._aiBubbleEl = bubbleEl;
    state._aiBubbleEl._raw = "";
    bubbleEl.classList.add("markdown-body");
    addCopyActions(msgEl, bubbleEl, contentEl, new Date());
    els.messages.appendChild(msgEl);
    _ensureMessagesBottomSentinel();
    removeThinkingMsg();  // streaming has started — thinking indicator no longer needed
    _setAiStreamingState(true);
  }
  _setAiStreamingState(true);
  state._aiBubbleEl._raw = (state._aiBubbleEl._raw || "") + String(text || "");
  _queueAiBubbleRender();
}

/**
 * Replace (not append) the current in-progress AI bubble with *text*.
 * Used during session replay to restore accumulated text without duplication.
 */
export function setChunkText(text) {
  if (!state._aiMsgEl) {
    const { msgEl, bubbleEl, contentEl } = createMsgBubble("ai");
    state._aiMsgEl    = msgEl;
    state._aiBubbleEl = bubbleEl;
    bubbleEl.classList.add("markdown-body");
    addCopyActions(msgEl, bubbleEl, contentEl, new Date());
    els.messages.appendChild(msgEl);
  }
  state._aiBubbleEl._raw = String(text || "");
  _setAiStreamingState(true);
  _queueAiBubbleRender();
  pinThinkingMsgToBottom();
}

export function completeAiMsgWithAuthoritativeText(text) {
  const authoritative = String(text || "");
  if (authoritative) setChunkText(authoritative);
  finalizeAiMsgNow();
}

export function finalizeAiMsg() {
  if (_streamRenderQueued) {
    _renderAiBubbleNow();
  }
  _finishAiMessageNow();
}

export function finalizeAiMsgNow() {
  if (_streamRenderQueued) {
    _renderAiBubbleNow();
  }
  _finishAiMessageNow();
}

export function discardActiveAiMsg() {
  _clearStreamTimers();
  removeThinkingMsg();
  if (state._aiMsgEl) state._aiMsgEl.remove();
  state._aiMsgEl = null;
  state._aiBubbleEl = null;
  _streamRenderQueued = false;
  _lastMarkdownRenderTs = 0;
  _autoScroll = true;
  _updateJumpBtn();
}

export function hasActiveAiMessage() {
  return !!(state._aiMsgEl && state._aiBubbleEl);
}

// ── Thinking indicator ─────────────────────────────────────────────────────

export function insertThinkingMsg(startTime = Date.now()) {
  removeThinkingMsg();
  const { msgEl, bubbleEl } = createMsgBubble("ai");
  let lastSec = -1;
  const updateThinkingText = () => {
    if (!state._thinkingEl) return;
    const sec = Math.floor((Date.now() - state._thinkingStart) / 1000);
    if (sec === lastSec) return;
    lastSec = sec;
    const spin = SPINNERS[_spinIdx++ % SPINNERS.length];
    bubbleEl.textContent = `${spin} thinking ${sec}s`;
  };
  bubbleEl.classList.add("thinking-bubble");
  bubbleEl.textContent = "⠋ thinking…";
  els.messages.appendChild(msgEl);
  _ensureMessagesBottomSentinel();
  scrollToBottom();
  state._thinkingEl    = msgEl;
  state._thinkingStart = startTime;
  updateThinkingText();
  state._thinkingTimer = setInterval(updateThinkingText, 1000);
}

export function removeThinkingMsg() {
  if (state._thinkingTimer) { clearInterval(state._thinkingTimer); state._thinkingTimer = null; }
  if (state._thinkingEl)    { state._thinkingEl.remove(); state._thinkingEl = null; }
}

export function pinThinkingMsgToBottom() {
  if (state._thinkingEl) {
    els.messages.appendChild(state._thinkingEl);
    _ensureMessagesBottomSentinel();
  }
}

export function hasVisibleInProgressMarker() {
  if (state._thinkingEl && state._thinkingEl.isConnected) return true;
  return !!els.messages.querySelector(".tool-line:not(.has-result)");
}

export function rehydrateInProgressUi(sessionId) {
  if (!isSessionRunning(sessionId)) return;
  const startedAt = state._sessionRunState[sessionId]?.startedAt || Date.now();
  if (!hasVisibleInProgressMarker()) {
    insertThinkingMsg(startedAt);
    return;
  }
  state._thinkingStart = startedAt;
  pinThinkingMsgToBottom();
  scrollToBottom();
}

// Markdown rendering is implemented in modules/markdown.js

// ── Session history restore ────────────────────────────────────────────────

function _fmtHistoryTs(rawTs) {
  const ts = Number(rawTs || 0);
  if (!Number.isFinite(ts) || ts <= 0) return "";
  return new Date(ts * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function _renderSessionSummary(summary) {
  if (!summary) return;
  const { msgEl, bubbleEl, contentEl } = createMsgBubble("system");
  msgEl.classList.add("session-history-block");
  bubbleEl.classList.add("session-history-summary");
  bubbleEl.innerHTML = `<div class="session-history-label">Compaction Summary</div><div class="session-history-markdown"></div>`;
  const summaryEl = bubbleEl.querySelector(".session-history-markdown");
  setMarkdownContent(summaryEl, summary, { surface: "chat" });
  bubbleEl._raw = summary;
  addCopyActions(msgEl, bubbleEl, contentEl, new Date());
  els.messages.appendChild(msgEl);
  _ensureMessagesBottomSentinel();
}

function _renderSessionLineage(lineage) {
  if (!Array.isArray(lineage) || !lineage.length) return;
  const { msgEl, bubbleEl } = createMsgBubble("system");
  msgEl.classList.add("session-history-block");
  bubbleEl.classList.add("session-history-lineage");
  const items = lineage.map((entry) => {
    const meta = entry.meta_json || {};
    const parts = [];
    if (entry.relationship) parts.push(String(entry.relationship));
    if (meta.archived != null || meta.kept != null) {
      parts.push(`archived ${Number(meta.archived || 0)}`);
      parts.push(`kept ${Number(meta.kept || 0)}`);
    }
    return `
      <div class="session-lineage-item">
        <div class="session-lineage-title">${escHtml(parts.join(" · "))}</div>
        <div class="session-lineage-meta">${escHtml(_fmtHistoryTs(entry.ts) || "unknown time")}</div>
      </div>
    `;
  }).join("");
  bubbleEl.innerHTML = `
    <div class="session-history-label">Lineage</div>
    <div class="session-lineage-list">${items}</div>
  `;
  els.messages.appendChild(msgEl);
  _ensureMessagesBottomSentinel();
}

function _applyMessageMetadata(msgEl, metaEl, t) {
  const messageId = String(t.message_id || t.turn_id || t.source_event_id || "").trim();
  if (messageId) msgEl.dataset.messageId = messageId;
  if (t.excluded) {
    msgEl.classList.add("is-excluded");
    msgEl.dataset.excluded = "1";
    if (metaEl && !metaEl.querySelector(".msg-state-pill")) {
      const pill = document.createElement("span");
      pill.className = "msg-state-pill";
      pill.textContent = "excluded";
      metaEl.appendChild(pill);
    }
  }
}

function _renderOneTurn(t) {
  const ts = _turnDate(t);
  if (t.role === "user") {
    const { msgEl, bubbleEl, metaEl, contentEl } = createMsgBubble("user");
    _applyMessageMetadata(msgEl, metaEl, t);
    _setBubbleMarkdownContent(bubbleEl, t.content || "", { surface: "chat", className: "bubble markdown-body" }, t.references || []);
    addCopyActions(msgEl, bubbleEl, contentEl, ts);
    els.messages.appendChild(msgEl);
    _ensureMessagesBottomSentinel();
  } else if (t.role === "assistant") {
    const { msgEl, bubbleEl, metaEl, contentEl } = createMsgBubble("ai");
    _applyMessageMetadata(msgEl, metaEl, t);
    setMarkdownContent(bubbleEl, t.content || "", { surface: "chat", className: "bubble markdown-body" });
      addCopyActions(msgEl, bubbleEl, contentEl, ts);
    els.messages.appendChild(msgEl);
    _ensureMessagesBottomSentinel();
  } else if (t.role === "tool") {
    const el = document.createElement("div");
    renderToolResult(el, t.tool_name || "tool", t.content || "");
    els.messages.appendChild(el);
    _ensureMessagesBottomSentinel();
  }
}

export async function renderSessionHistory(session_id, turns, summary = "", lineage = []) {
  const keepInProgress = isSessionRunning(session_id);
  debugUiLifecycle("render_session_history", {
    session_id,
    running: keepInProgress,
    turns: turns?.length || 0,
  });
  if (!keepInProgress) removeThinkingMsg();
  _cancelHistoryBottomReveal();
  els.messages.innerHTML = "";
  _ensureMessagesBottomSentinel();
  state._aiMsgEl     = null;
  state._aiBubbleEl  = null;
  state._lastUserMsgEl = null;
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex   = 0;
  resetActiveRound();

  setCurrentSessionId(session_id);

  _renderSessionSummary(summary);
  _renderSessionLineage(lineage);

  if (!turns.length && !summary && !(lineage || []).length) {
    insertSystemMsg("No history for this session.");
    refreshChatStats();
    return;
  }

  // Render turns in batches to avoid blocking the main thread on long sessions.
  // Each batch yields control back to the browser so the UI stays responsive.
  const BATCH_SIZE = 15;
  els.messages.classList.add("no-msg-anim");
  for (let i = 0; i < turns.length; i++) {
    _renderOneTurn(turns[i]);
    if ((i + 1) % BATCH_SIZE === 0 && i + 1 < turns.length) {
      await new Promise((r) => setTimeout(r, 0));
    }
  }
  els.messages.classList.remove("no-msg-anim");
  if (keepInProgress) rehydrateInProgressUi(session_id);
  refreshChatStats();

  const shouldScrollToLatest = _historyBottomRequests.delete(session_id);
  const savedTop = _scrollMap.get(session_id);
  if (shouldScrollToLatest) {
    _startHistoryBottomReveal(session_id);
  } else if (keepInProgress || savedTop == null) {
    scrollToBottom();
  } else {
    els.messages.scrollTop = savedTop;
    _autoScroll = _isNearBottom();
    _updateJumpBtn();
  }
}

// ── New session ────────────────────────────────────────────────────────────

export function newSession() {
  resetChatSessionUiState();
  insertSystemMsg("New session started. Use this when you switch to a new topic.");
}

export function resetChatSessionUiState() {
  _cancelHistoryBottomReveal();
  removeThinkingMsg();
  state._pendingSessionStart = false;
  clearCurrentSessionId();
  state.inTokens   = 0;
  state.outTokens  = 0;
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex   = 0;
  state._aiMsgEl     = null;
  state._aiBubbleEl  = null;
  resetActiveRound();
  els.messages.innerHTML = "";
  els.tokenStats.textContent   = "";
  document.querySelectorAll(".sidebar-session").forEach((el) => el.classList.remove("active"));
  refreshChatStats();
}
