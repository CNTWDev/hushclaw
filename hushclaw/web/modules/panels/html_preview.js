/**
 * panels/html_preview.js — Live HTML preview panel (bottom split of #chat-area).
 * Opens automatically when a ```html block is detected in a streaming response.
 * Uses srcdoc + 200ms debounce for flicker-free incremental updates.
 */

let _debounceTimer = null;
let _lastHtml = "";

// ── Public API ─────────────────────────────────────────────────────────────

export function initHtmlPreview() {
  document.getElementById("btn-close-html-preview")
    ?.addEventListener("click", closeHtmlPreview);
  document.getElementById("btn-popout-html-preview")
    ?.addEventListener("click", _popout);
}

/** Called on every streaming chunk — debounced. */
export function updateHtmlPreview(rawMarkdown) {
  const html = _extractLastHtmlBlock(rawMarkdown);
  if (!html) {
    hideHtmlPreview();
    return;
  }
  _lastHtml = html;
  _showPanel();
  if (_debounceTimer) return;
  _debounceTimer = setTimeout(() => {
    _setIframe(_lastHtml);
    _debounceTimer = null;
  }, 200);
}

/** Called when streaming finishes — always fires a final render. */
export function finalizeHtmlPreview(rawMarkdown) {
  clearTimeout(_debounceTimer);
  _debounceTimer = null;
  const html = _extractLastHtmlBlock(rawMarkdown);
  if (!html) {
    hideHtmlPreview();
    return;
  }
  _lastHtml = html;
  _showPanel();
  _setIframe(html);
}

export function closeHtmlPreview() {
  clearTimeout(_debounceTimer);
  _debounceTimer = null;
  _hidePanelPreservingMessagesScroll();
}

export function hideHtmlPreview() {
  _lastHtml = "";
  clearTimeout(_debounceTimer);
  _debounceTimer = null;
  const iframe = document.getElementById("html-preview-iframe");
  if (iframe) iframe.srcdoc = "";
  document.getElementById("html-preview-panel")?.classList.add("hidden");
}

// ── Internals ──────────────────────────────────────────────────────────────

function _extractLastHtmlBlock(raw) {
  // Match last complete ```html ... ``` block
  let best = null;
  const re = /```html\n([\s\S]*?)```/g;
  let m;
  while ((m = re.exec(raw)) !== null) best = m[1];
  if (best !== null) return best.trim();

  // Partial block (no closing fence) — used during streaming
  const idx = raw.lastIndexOf("```html\n");
  if (idx === -1) return null;
  const partial = raw.slice(idx + 8).trim();
  return partial || null;
}

function _showPanel() {
  document.getElementById("html-preview-panel")?.classList.remove("hidden");
}

function _hidePanelPreservingMessagesScroll() {
  const panel = document.getElementById("html-preview-panel");
  if (!panel || panel.classList.contains("hidden")) return;
  const messages = document.getElementById("messages");
  const beforeHeight = messages?.scrollHeight || 0;
  const beforeTop = messages?.scrollTop || 0;
  const wasNearBottom = messages
    ? messages.scrollHeight - messages.scrollTop - messages.clientHeight < 80
    : false;

  panel.classList.add("hidden");

  if (!messages) return;
  requestAnimationFrame(() => {
    if (wasNearBottom) {
      messages.scrollTop = messages.scrollHeight;
      return;
    }
    const delta = messages.scrollHeight - beforeHeight;
    messages.scrollTop = Math.max(0, beforeTop + delta);
  });
}

function _setIframe(html) {
  const iframe = document.getElementById("html-preview-iframe");
  if (iframe) iframe.srcdoc = html;
}

function _popout() {
  if (!_lastHtml) return;
  const win = window.open("", "_blank");
  if (win) {
    win.document.write(_lastHtml);
    win.document.close();
  }
}
