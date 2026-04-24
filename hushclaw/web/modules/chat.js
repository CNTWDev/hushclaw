/**
 * chat.js — Chat message rendering, thinking indicator, session history.
 *
 * Tool-related rendering lives in chat/tools.js.
 * Copy/export/share actions live in chat/export.js.
 */

import {
  state, els, SPINNERS, escHtml,
  isSessionRunning, setCurrentSessionId, clearCurrentSessionId, debugUiLifecycle,
} from "./state.js";
import { renderMarkdown } from "./markdown.js";

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
  if (_autoScroll) els.messages.scrollTop = els.messages.scrollHeight;
  _updateJumpBtn();
}

els.messages.addEventListener("scroll", () => {
  if (_isNearBottom()) {
    _autoScroll = true;
  } else if (state._aiMsgEl) {
    _autoScroll = false;  // pause only while streaming
  }
  _updateJumpBtn();
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
  _autoScroll = true;
  els.messages.scrollTop = els.messages.scrollHeight;
  _updateJumpBtn();
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

// ── Chat message helpers ───────────────────────────────────────────────────

export function insertUserMsg(text) {
  const { msgEl, bubbleEl, contentEl } = createMsgBubble("user");
  bubbleEl.classList.add("markdown-body");
  bubbleEl._raw = text;
  bubbleEl.innerHTML = renderMarkdown(text);
  addCopyActions(msgEl, bubbleEl, contentEl, new Date());
  els.messages.appendChild(msgEl);
  scrollToBottom();
}

export function insertSystemMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("system");
  bubbleEl.textContent = text;
  els.messages.appendChild(msgEl);
  scrollToBottom();
}

export function insertErrorMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("error");
  bubbleEl.textContent = "Error: " + text;
  els.messages.appendChild(msgEl);
  scrollToBottom();
}

// ── Streaming AI response ──────────────────────────────────────────────────

export function appendChunk(text) {
  if (!state._aiMsgEl) {
    const { msgEl, bubbleEl, contentEl } = createMsgBubble("ai");
    state._aiMsgEl    = msgEl;
    state._aiBubbleEl = bubbleEl;
    bubbleEl.classList.add("markdown-body");
    addCopyActions(msgEl, bubbleEl, contentEl, new Date());
    els.messages.appendChild(msgEl);
    removeThinkingMsg();  // streaming has started — thinking indicator no longer needed
  }
  state._aiBubbleEl._raw = (state._aiBubbleEl._raw || "") + text;
  state._aiBubbleEl.innerHTML = renderMarkdown(state._aiBubbleEl._raw);
  _scrollToBottomIfAuto();
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
  state._aiBubbleEl._raw = text;
  state._aiBubbleEl.innerHTML = renderMarkdown(text);
  pinThinkingMsgToBottom();
  _scrollToBottomIfAuto();
}

export function finalizeAiMsg() {
  removeThinkingMsg();
  finalizeActiveRound();
  if (state._aiMsgEl && !state._aiBubbleEl?._raw?.trim()) {
    state._aiMsgEl.remove();
  }
  state._aiMsgEl    = null;
  state._aiBubbleEl = null;
  _autoScroll = true;
  _updateJumpBtn();
}

// ── Thinking indicator ─────────────────────────────────────────────────────

export function insertThinkingMsg(startTime = Date.now()) {
  removeThinkingMsg();
  const { msgEl, bubbleEl } = createMsgBubble("ai");
  bubbleEl.classList.add("thinking-bubble");
  bubbleEl.textContent = "⠋ thinking…";
  els.messages.appendChild(msgEl);
  scrollToBottom();
  state._thinkingEl    = msgEl;
  state._thinkingStart = startTime;
  state._thinkingTimer = setInterval(() => {
    if (!state._thinkingEl) return;
    const sec  = Math.floor((Date.now() - state._thinkingStart) / 1000);
    const spin = SPINNERS[_spinIdx++ % SPINNERS.length];
    bubbleEl.textContent = `${spin} thinking ${sec}s`;
  }, 100);
}

export function removeThinkingMsg() {
  if (state._thinkingTimer) { clearInterval(state._thinkingTimer); state._thinkingTimer = null; }
  if (state._thinkingEl)    { state._thinkingEl.remove(); state._thinkingEl = null; }
}

export function pinThinkingMsgToBottom() {
  if (state._thinkingEl) {
    els.messages.appendChild(state._thinkingEl);
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
  bubbleEl.classList.add("markdown-body", "session-history-summary");
  bubbleEl._raw = summary;
  bubbleEl.innerHTML = `<div class="session-history-label">Compaction Summary</div>${renderMarkdown(summary)}`;
  addCopyActions(msgEl, bubbleEl, contentEl, new Date());
  els.messages.appendChild(msgEl);
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
}

export function renderSessionHistory(session_id, turns, summary = "", lineage = []) {
  const keepInProgress = isSessionRunning(session_id);
  debugUiLifecycle("render_session_history", {
    session_id,
    running: keepInProgress,
    turns: turns?.length || 0,
  });
  if (!keepInProgress) removeThinkingMsg();
  els.messages.innerHTML = "";
  state._aiMsgEl     = null;
  state._aiBubbleEl  = null;
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex   = 0;
  resetActiveRound();

  setCurrentSessionId(session_id);

  _renderSessionSummary(summary);
  _renderSessionLineage(lineage);

  if (!turns.length && !summary && !(lineage || []).length) {
    insertSystemMsg("No history for this session.");
    return;
  }

  for (const t of turns) {
    const ts = t.ts ? new Date(t.ts * 1000) : new Date();
    if (t.role === "user") {
      const { msgEl, bubbleEl, contentEl } = createMsgBubble("user");
      bubbleEl.classList.add("markdown-body");
      bubbleEl._raw = t.content || "";
      bubbleEl.innerHTML = renderMarkdown(bubbleEl._raw);
      addCopyActions(msgEl, bubbleEl, contentEl, ts);
      els.messages.appendChild(msgEl);
    } else if (t.role === "assistant") {
      const { msgEl, bubbleEl, contentEl } = createMsgBubble("ai");
      bubbleEl.classList.add("markdown-body");
      bubbleEl._raw = t.content || "";
      bubbleEl.innerHTML = renderMarkdown(bubbleEl._raw);
      addCopyActions(msgEl, bubbleEl, contentEl, ts);
      els.messages.appendChild(msgEl);
    } else if (t.role === "tool") {
      const el = document.createElement("div");
      renderToolResult(el, t.tool_name || "tool", t.content || "");
      els.messages.appendChild(el);
    }
  }
  if (keepInProgress) rehydrateInProgressUi(session_id);
  scrollToBottom();
}

// ── New session ────────────────────────────────────────────────────────────

export function newSession() {
  resetChatSessionUiState();
  insertSystemMsg("New session started. Use this when you switch to a new topic.");
}

export function resetChatSessionUiState() {
  removeThinkingMsg();
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
}
