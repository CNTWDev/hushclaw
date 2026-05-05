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
import { renderMarkdown, getHtmlBlock } from "./markdown.js";
import { hideHtmlPreview } from "./panels/html_preview.js";

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
let _typewriterRaf = 0;
let _typewriterLastTs = 0;
let _typewriterCarry = 0;
let _typewriterPendingChars = [];
let _pendingFinalizeAiMsg = false;
let _lastVisibleStreamChars = 0;
let _lastMarkdownRenderTs = 0;

const _TYPEWRITER_BASE_CPS = 56;
const _TYPEWRITER_MAX_CPS = 168;
const _TYPEWRITER_BACKLOG_DIVISOR = 10;
const _TYPEWRITER_MAX_CHARS_PER_FRAME = 12;

// ── Inline HTML preview (iframe injection) ────────────────────────────────────
// bubble.innerHTML is rebuilt on every chunk; iframes would be destroyed each time.
// We cache them by content key so they survive re-renders without reloading.
const _iframeCache = new WeakMap(); // bubble el → Map<key, wrapper el>
const _previewIframeRegistry = new Map(); // previewId -> iframe el
let _previewBridgeBound = false;

// Hidden "parking lot": iframe wrappers are moved here before bubble.innerHTML is
// rebuilt so they stay attached to the document tree. Removing an iframe from the
// document and re-adding it triggers a browser reload; parking prevents that.
const _iframeParkingLot = (() => {
  const el = document.createElement("div");
  el.style.cssText = "position:fixed;top:-9999px;left:-9999px;width:0;height:0;overflow:hidden;pointer-events:none;visibility:hidden;";
  document.body.appendChild(el);
  return el;
})();

function _parkCachedHtmlPreviews(bubbleEl) {
  const activeCache = _iframeCache.get(bubbleEl);
  if (activeCache) activeCache.forEach(wrap => _iframeParkingLot.appendChild(wrap));
}

function _lastCompleteHtmlBlockEnd(raw) {
  const re = /```(?:html|mermaid)\n[\s\S]*?```/g;
  let end = -1;
  let m;
  while ((m = re.exec(raw)) !== null) {
    end = m.index + m[0].length;
  }
  return end;
}

function _renderAiBubbleWithStableHtmlPrefix(bubbleEl, raw) {
  const stableEnd = _lastCompleteHtmlBlockEnd(raw);
  if (stableEnd <= 0 || stableEnd >= raw.length) {
    bubbleEl._streamSplitActive = false;
    bubbleEl._streamStablePrefixRaw = "";
    return false;
  }

  const prefixRaw = raw.slice(0, stableEnd);
  const tailRaw = raw.slice(stableEnd);
  let tailEl = bubbleEl.querySelector(":scope > .stream-tail");

  if (!bubbleEl._streamSplitActive || bubbleEl._streamStablePrefixRaw !== prefixRaw || !tailEl) {
    _parkCachedHtmlPreviews(bubbleEl);
    bubbleEl.innerHTML = "";

    const prefixEl = document.createElement("div");
    prefixEl.className = "stream-stable-prefix";
    prefixEl.innerHTML = renderMarkdown(prefixRaw);

    tailEl = document.createElement("div");
    tailEl.className = "stream-tail";
    tailEl.innerHTML = renderMarkdown(tailRaw);

    bubbleEl.appendChild(prefixEl);
    bubbleEl.appendChild(tailEl);
    bubbleEl._streamSplitActive = true;
    bubbleEl._streamStablePrefixRaw = prefixRaw;
    injectHtmlPreviews(bubbleEl);
    return true;
  }

  tailEl.innerHTML = renderMarkdown(tailRaw);
  return true;
}

function _syncHtmlPreviewMessageState(bubble) {
  const msgEl = bubble.closest(".msg");
  if (!msgEl) return;
  const hasPreview = !!bubble.querySelector(".html-inline-preview[data-htmlkey]");
  msgEl.classList.toggle("msg-has-html-preview", hasPreview);
}

function _clearStreamTimers() {
  if (_streamCaretHideTimer) {
    clearTimeout(_streamCaretHideTimer);
    _streamCaretHideTimer = null;
  }
}

function _clearTypewriterLoop() {
  if (_typewriterRaf) {
    cancelAnimationFrame(_typewriterRaf);
    _typewriterRaf = 0;
  }
  _typewriterLastTs = 0;
  _typewriterCarry = 0;
}

function _measureVisibleStreamChars(bubbleEl) {
  if (!bubbleEl) return 0;
  _removeStreamCaret(bubbleEl);
  return bubbleEl.textContent?.length || 0;
}

function _removeStreamCaret(bubbleEl) {
  bubbleEl?.querySelector(".stream-caret")?.remove();
}

function _wrapTrailingVisibleChars(bubbleEl, count) {
  if (!bubbleEl || count <= 0) return;
  const textNodes = [];
  const walker = document.createTreeWalker(bubbleEl, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node?.nodeValue) return NodeFilter.FILTER_REJECT;
      const parent = node.parentElement;
      if (!parent || parent.closest(".stream-caret")) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    textNodes.push(node);
  }
  let remaining = count;
  for (let i = textNodes.length - 1; i >= 0 && remaining > 0; i -= 1) {
    let node = textNodes[i];
    const len = node.nodeValue?.length || 0;
    if (!len) continue;
    const take = Math.min(len, remaining);
    if (take < len) {
      node = node.splitText(len - take);
    }
    const span = document.createElement("span");
    span.className = "stream-chunk-reveal";
    node.parentNode?.insertBefore(span, node);
    span.appendChild(node);
    remaining -= take;
  }
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

  // Throttle markdown re-parse to ~12/s while chars are still incoming.
  // Full re-parse on every RAF frame is O(n) in message length; capping at
  // 80 ms intervals keeps the typewriter animation smooth without stalling.
  if (_typewriterPendingChars.length > 0 && Date.now() - _lastMarkdownRenderTs < 80) {
    _ensureStreamCaret(bubbleEl);
    _scrollToBottomIfAuto();
    _queueAiBubbleRender();
    return;
  }

  const raw = bubbleEl._raw || "";
  const usedStableHtmlPrefix = _renderAiBubbleWithStableHtmlPrefix(bubbleEl, raw);
  if (!usedStableHtmlPrefix) {
    // Park cached iframe wrappers into the hidden lot so they stay attached to
    // the document during full innerHTML rebuilds.
    _parkCachedHtmlPreviews(bubbleEl);
    bubbleEl.innerHTML = renderMarkdown(raw);
    injectHtmlPreviews(bubbleEl);
  }
  _lastMarkdownRenderTs = Date.now();
  const visibleChars = _measureVisibleStreamChars(bubbleEl);
  const newlyVisibleChars = Math.max(0, visibleChars - _lastVisibleStreamChars);
  if (newlyVisibleChars > 0) {
    _wrapTrailingVisibleChars(bubbleEl, newlyVisibleChars);
  }
  _lastVisibleStreamChars = visibleChars;
  _ensureStreamCaret(bubbleEl);
  _animateStreamChunk(bubbleEl);
  _scrollToBottomIfAuto();
}

function _queueAiBubbleRender() {
  if (_streamRenderQueued) return;
  _streamRenderQueued = true;
  requestAnimationFrame(_renderAiBubbleNow);
}

function _queueTypewriterChars(text) {
  if (!text) return;
  _typewriterPendingChars.push(...Array.from(text));
}

function _flushTypewriterPendingChars() {
  if (!state._aiBubbleEl || !_typewriterPendingChars.length) return;
  state._aiBubbleEl._raw = (state._aiBubbleEl._raw || "") + _typewriterPendingChars.join("");
  _typewriterPendingChars = [];
  _clearTypewriterLoop();
  _renderAiBubbleNow();
}

function _finishAiMessageNow() {
  _typewriterPendingChars = [];
  _clearTypewriterLoop();
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
  _pendingFinalizeAiMsg = false;
  _lastVisibleStreamChars = 0;
  _lastMarkdownRenderTs = 0;
  _autoScroll = true;
  _updateJumpBtn();
}

function _stepTypewriter(ts) {
  _typewriterRaf = 0;
  if (!state._aiBubbleEl) {
    _typewriterPendingChars = [];
    _pendingFinalizeAiMsg = false;
    _clearTypewriterLoop();
    return;
  }
  if (!_typewriterLastTs) _typewriterLastTs = ts;
  const dt = Math.max(0, ts - _typewriterLastTs);
  _typewriterLastTs = ts;

  const backlog = _typewriterPendingChars.length;
  if (backlog) {
    const cps = Math.min(_TYPEWRITER_MAX_CPS, _TYPEWRITER_BASE_CPS + backlog / _TYPEWRITER_BACKLOG_DIVISOR);
    _typewriterCarry += dt * cps / 1000;
    let count = Math.floor(_typewriterCarry);
    if (count <= 0) count = 1;
    count = Math.min(count, backlog, _TYPEWRITER_MAX_CHARS_PER_FRAME);
    _typewriterCarry = Math.max(0, _typewriterCarry - count);
    const nextText = _typewriterPendingChars.splice(0, count).join("");
    state._aiBubbleEl._raw = (state._aiBubbleEl._raw || "") + nextText;
    _queueAiBubbleRender();
  }

  if (_typewriterPendingChars.length) {
    _typewriterRaf = requestAnimationFrame(_stepTypewriter);
    return;
  }

  _clearTypewriterLoop();
  if (_pendingFinalizeAiMsg) _finishAiMessageNow();
}

function _ensureTypewriterLoop() {
  if (_typewriterRaf || !_typewriterPendingChars.length) return;
  _typewriterRaf = requestAnimationFrame(_stepTypewriter);
}

function _bindInlinePreviewBridge() {
  if (_previewBridgeBound) return;
  _previewBridgeBound = true;
  window.addEventListener("message", (event) => {
    const data = event?.data;
    if (!data || data.type !== "hc-html-preview-size" || !data.previewId) return;
    const iframe = _previewIframeRegistry.get(data.previewId);
    if (!iframe) return;
    const nextHeight = Math.max(320, Number(data.height) || 0);
    iframe.style.height = `${nextHeight}px`;
  });
}

function _buildInlinePreviewSrcdoc(previewId, html) {
  const style = `
<style>
html, body {
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
  background: #fff;
}
body {
  transform-origin: top left;
  max-width: none !important;
}
</style>`;
  const script = `
<script>
(() => {
  const PREVIEW_ID = ${JSON.stringify(previewId)};
  const sendSize = () => {
    try {
      const de = document.documentElement;
      const body = document.body || de;
      body.style.transform = "none";
      body.style.width = "auto";
      const fullWidth = Math.max(
        1,
        de.scrollWidth || 0,
        de.offsetWidth || 0,
        body.scrollWidth || 0,
        body.offsetWidth || 0
      );
      const viewportWidth = Math.max(1, de.clientWidth || 0, window.innerWidth || 0);
      const scale = Math.min(1, viewportWidth / fullWidth);
      body.style.width = fullWidth + "px";
      body.style.transform = scale < 0.999 ? "scale(" + scale + ")" : "none";
      const fullHeight = Math.max(
        de.scrollHeight || 0,
        de.offsetHeight || 0,
        body.scrollHeight || 0,
        body.offsetHeight || 0
      );
      const height = Math.ceil(fullHeight * scale);
      parent.postMessage({ type: "hc-html-preview-size", previewId: PREVIEW_ID, height }, "*");
    } catch (_err) {}
  };
  const schedule = () => {
    requestAnimationFrame(() => setTimeout(sendSize, 0));
  };
  if ("ResizeObserver" in window) {
    const ro = new ResizeObserver(schedule);
    ro.observe(document.documentElement);
    if (document.body) ro.observe(document.body);
  }
  new MutationObserver(schedule).observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    characterData: true,
  });
  window.addEventListener("load", schedule);
  window.addEventListener("resize", schedule);
  schedule();
  setTimeout(schedule, 80);
  setTimeout(schedule, 260);
})();
</script>`;

  if (/<html[\s>]/i.test(html) || /<body[\s>]/i.test(html) || /<head[\s>]/i.test(html)) {
    let out = html;
    if (/<head[\s>]/i.test(out)) out = out.replace(/<head([^>]*)>/i, `<head$1>${style}`);
    else out = style + out;
    if (/<\/body>/i.test(out)) {
      out = out.replace(/<\/body>/i, `${script}</body>`);
    } else {
      // Truncated HTML (no </body>) — close any dangling <script> block so the
      // resize script doesn't end up as text content inside an open script tag.
      const lastOpen = out.search(/<script(?:\s[^>]*)?>(?![\s\S]*<\/script>)/i);
      if (lastOpen !== -1) out += "\n});\n</script>";
      out += script;
    }
    return out;
  }

  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    ${style}
  </head>
  <body>
    ${html}
    ${script}
  </body>
</html>`;
}

export function injectHtmlPreviews(bubble) {
  _bindInlinePreviewBridge();
  bubble.querySelectorAll(".html-inline-preview[data-htmlkey]").forEach(div => {
    // Skip in-flight partial blocks — the key changes every chunk, so creating
    // an iframe now would only get discarded on the next render. Wait until the
    // closing fence arrives and the block becomes complete (no html-preview-partial class).
    if (div.classList.contains("html-preview-partial")) return;
    const key = div.dataset.htmlkey;
    const html = getHtmlBlock(key);
    if (!html) return;
    let cache = _iframeCache.get(bubble);
    if (!cache) { cache = new Map(); _iframeCache.set(bubble, cache); }
    if (!cache.has(key)) {
      const wrap = document.createElement("div");
      wrap.className = "html-inline-preview-inner";
      const toolbar = document.createElement("div");
      toolbar.className = "html-preview-toolbar";
      const popBtn = document.createElement("button");
      popBtn.className = "muted-btn small";
      popBtn.title = "Open in new tab";
      popBtn.textContent = "↗";
      popBtn.addEventListener("click", () => {
        const w = window.open("", "_blank");
        if (w) { w.document.write(html); w.document.close(); }
      });
      toolbar.appendChild(popBtn);
      const iframe = document.createElement("iframe");
      iframe.setAttribute("sandbox", "allow-scripts");
      iframe.setAttribute("scrolling", "no");
      const previewId = `hpv_${key}`;
      iframe.dataset.previewId = previewId;
      iframe.srcdoc = _buildInlinePreviewSrcdoc(previewId, html);
      _previewIframeRegistry.set(previewId, iframe);
      wrap.appendChild(toolbar);
      wrap.appendChild(iframe);
      cache.set(key, wrap);
    }
    div.appendChild(cache.get(key));
  });
  _syncHtmlPreviewMessageState(bubble);
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
  injectHtmlPreviews(bubbleEl);
  addCopyActions(msgEl, bubbleEl, contentEl, new Date());
  els.messages.appendChild(msgEl);
  state._lastUserMsgEl = msgEl;
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
    state._aiBubbleEl._raw = "";
    _lastVisibleStreamChars = 0;
    bubbleEl.classList.add("markdown-body");
    addCopyActions(msgEl, bubbleEl, contentEl, new Date());
    els.messages.appendChild(msgEl);
    hideHtmlPreview();
    removeThinkingMsg();  // streaming has started — thinking indicator no longer needed
    _setAiStreamingState(true);
  }
  _pendingFinalizeAiMsg = false;
  _setAiStreamingState(true);
  _queueTypewriterChars(text);
  _ensureTypewriterLoop();
}

/**
 * Replace (not append) the current in-progress AI bubble with *text*.
 * Used during session replay to restore accumulated text without duplication.
 */
export function setChunkText(text) {
  _pendingFinalizeAiMsg = false;
  _typewriterPendingChars = [];
  _clearTypewriterLoop();
  if (!state._aiMsgEl) {
    const { msgEl, bubbleEl, contentEl } = createMsgBubble("ai");
    state._aiMsgEl    = msgEl;
    state._aiBubbleEl = bubbleEl;
    _lastVisibleStreamChars = 0;
    bubbleEl.classList.add("markdown-body");
    addCopyActions(msgEl, bubbleEl, contentEl, new Date());
    els.messages.appendChild(msgEl);
  }
  state._aiBubbleEl._raw = text;
  _setAiStreamingState(true);
  _renderAiBubbleNow();
  pinThinkingMsgToBottom();
}

export function finalizeAiMsg() {
  if (_typewriterPendingChars.length) {
    _pendingFinalizeAiMsg = true;
    _ensureTypewriterLoop();
    return;
  }
  if (_streamRenderQueued) {
    _renderAiBubbleNow();
  }
  _finishAiMessageNow();
}

export function finalizeAiMsgNow() {
  _flushTypewriterPendingChars();
  if (_streamRenderQueued) {
    _renderAiBubbleNow();
  }
  _finishAiMessageNow();
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
  injectHtmlPreviews(bubbleEl);
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
  const ts = t.ts ? new Date(t.ts * 1000) : new Date();
  if (t.role === "user") {
    const { msgEl, bubbleEl, metaEl, contentEl } = createMsgBubble("user");
    _applyMessageMetadata(msgEl, metaEl, t);
    bubbleEl.classList.add("markdown-body");
    bubbleEl._raw = t.content || "";
    bubbleEl.innerHTML = renderMarkdown(bubbleEl._raw);
    injectHtmlPreviews(bubbleEl);
    addCopyActions(msgEl, bubbleEl, contentEl, ts);
    els.messages.appendChild(msgEl);
  } else if (t.role === "assistant") {
    const { msgEl, bubbleEl, metaEl, contentEl } = createMsgBubble("ai");
    _applyMessageMetadata(msgEl, metaEl, t);
    bubbleEl.classList.add("markdown-body");
    bubbleEl._raw = t.content || "";
    bubbleEl.innerHTML = renderMarkdown(bubbleEl._raw);
    injectHtmlPreviews(bubbleEl);
    addCopyActions(msgEl, bubbleEl, contentEl, ts);
    els.messages.appendChild(msgEl);
  } else if (t.role === "tool") {
    const el = document.createElement("div");
    renderToolResult(el, t.tool_name || "tool", t.content || "");
    els.messages.appendChild(el);
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
  els.messages.innerHTML = "";
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
  hideHtmlPreview();
  if (keepInProgress) rehydrateInProgressUi(session_id);

  const savedTop = _scrollMap.get(session_id);
  if (keepInProgress || savedTop == null) {
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
  removeThinkingMsg();
  hideHtmlPreview();
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
