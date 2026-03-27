/**
 * chat.js — Chat message rendering, markdown, thinking indicator, session history.
 */

import { state, els, SPINNERS, escHtml, prettyJson, showToast } from "./state.js";
import { renderMarkdown } from "./markdown.js";

let _spinIdx = 0;
const COPY_IMAGE_WATERMARK = "HushClaw：传音开源的龙虾架构提供服务";
const HTML2CANVAS_URL = "https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js";
let _html2canvasLoading = null;

// ── Scrolling ──────────────────────────────────────────────────────────────

export function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

// ── Message bubble factory ─────────────────────────────────────────────────

export function createMsgBubble(kind) {
  const msgEl = document.createElement("div");
  msgEl.className = `msg ${kind}`;

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
  return { msgEl, bubbleEl, metaEl };
}

function setCopyBtnTempText(btn, text, fallback) {
  const prev = btn.textContent;
  btn.textContent = text;
  setTimeout(() => { btn.textContent = fallback || prev || ""; }, 1200);
}

function getCopyImageErrorMessage(err) {
  const msg = String(err?.message || err || "");
  const lower = msg.toLowerCase();
  if (lower.includes("notallowederror") || lower.includes("permission")) {
    return "Copy image failed: clipboard permission denied by browser.";
  }
  if (lower.includes("clipboarditem") || lower.includes("clipboard")) {
    return "Copy image failed: browser does not support image clipboard write.";
  }
  if (lower.includes("failed to load html2canvas")) {
    return "Copy image failed: fallback renderer could not be loaded (network/CSP).";
  }
  if (lower.includes("canvas") || lower.includes("png")) {
    return "Copy image failed: canvas render/export error.";
  }
  if (lower.includes("foreignobject") || lower.includes("svg")) {
    return "Copy image failed: browser could not rasterize styled content.";
  }
  return `Copy image failed: ${msg || "unknown error"}`;
}

async function renderNodeToPngBlob(node) {
  const rect = node.getBoundingClientRect();
  const width = Math.max(1, Math.ceil(rect.width));
  const height = Math.max(1, Math.ceil(rect.height));
  const scale = Math.max(1, Math.min(2, window.devicePixelRatio || 1));

  const cloned = node.cloneNode(true);
  cloned.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
  const xhtml = new XMLSerializer().serializeToString(cloned);
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">
      <foreignObject width="100%" height="100%">${xhtml}</foreignObject>
    </svg>
  `;
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const img = await new Promise((resolve, reject) => {
      const i = new Image();
      i.onload = () => resolve(i);
      i.onerror = reject;
      i.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(width * scale);
    canvas.height = Math.ceil(height * scale);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas context unavailable");
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0, width, height);
    return await new Promise((resolve, reject) => {
      canvas.toBlob((png) => {
        if (png) resolve(png);
        else reject(new Error("PNG encoding failed"));
      }, "image/png");
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function ensureHtml2Canvas() {
  if (window.html2canvas) return window.html2canvas;
  if (_html2canvasLoading) return _html2canvasLoading;
  _html2canvasLoading = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = HTML2CANVAS_URL;
    s.async = true;
    s.onload = () => {
      if (window.html2canvas) resolve(window.html2canvas);
      else reject(new Error("html2canvas loaded but unavailable"));
    };
    s.onerror = () => reject(new Error("Failed to load html2canvas"));
    document.head.appendChild(s);
  });
  return _html2canvasLoading;
}

async function renderNodeToPngBlobWithHtml2Canvas(node) {
  const html2canvas = await ensureHtml2Canvas();
  const canvas = await html2canvas(node, {
    backgroundColor: null,
    scale: Math.max(1, Math.min(2, window.devicePixelRatio || 1)),
    useCORS: true,
    logging: false,
  });
  return await new Promise((resolve, reject) => {
    canvas.toBlob((png) => {
      if (png) resolve(png);
      else reject(new Error("PNG encoding failed"));
    }, "image/png");
  });
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function copyBubbleAsImage(bubbleEl, btn) {
  const stage = document.createElement("div");
  stage.className = "copy-image-stage";
  const card = document.createElement("div");
  card.className = "copy-image-card";
  const bubbleClone = bubbleEl.cloneNode(true);
  const watermark = document.createElement("div");
  watermark.className = "copy-image-watermark";
  watermark.textContent = COPY_IMAGE_WATERMARK;
  card.appendChild(bubbleClone);
  card.appendChild(watermark);
  stage.appendChild(card);
  document.body.appendChild(stage);
  try {
    let blob;
    try {
      blob = await renderNodeToPngBlob(card);
    } catch {
      // Fallback for browsers where SVG foreignObject rasterization is unreliable.
      blob = await renderNodeToPngBlobWithHtml2Canvas(card);
    }
    if (navigator.clipboard?.write && window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      setCopyBtnTempText(btn, "Copied", "IMG");
      return;
    }
    downloadBlob(blob, "hushclaw-message.png");
    setCopyBtnTempText(btn, "Saved", "IMG");
    showToast("Clipboard image not supported. Downloaded PNG instead.", "warn");
  } finally {
    stage.remove();
  }
}

function addCopyActions(msgEl, bubbleEl, metaEl) {
  const actions = document.createElement("div");
  actions.className = "msg-copy-actions";

  const mdBtn = document.createElement("button");
  mdBtn.type = "button";
  mdBtn.className = "msg-copy-btn";
  mdBtn.textContent = "MD";
  mdBtn.title = "Copy original markdown";
  mdBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    const raw = bubbleEl._raw ?? bubbleEl.textContent ?? "";
    try {
      await navigator.clipboard.writeText(raw);
      setCopyBtnTempText(mdBtn, "Copied", "MD");
    } catch {
      setCopyBtnTempText(mdBtn, "Failed", "MD");
    }
  });

  const imgBtn = document.createElement("button");
  imgBtn.type = "button";
  imgBtn.className = "msg-copy-btn";
  imgBtn.textContent = "IMG";
  imgBtn.title = "Copy rendered message as image";
  imgBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    try {
      await copyBubbleAsImage(bubbleEl, imgBtn);
    } catch (err) {
      setCopyBtnTempText(imgBtn, "Failed", "IMG");
      showToast(getCopyImageErrorMessage(err), "error");
    }
  });

  actions.appendChild(mdBtn);
  actions.appendChild(imgBtn);
  metaEl.appendChild(actions);
}

// ── Chat message helpers ───────────────────────────────────────────────────

export function insertUserMsg(text) {
  const { msgEl, bubbleEl, metaEl } = createMsgBubble("user");
  bubbleEl.classList.add("markdown-body");
  bubbleEl._raw = text;
  bubbleEl.innerHTML = renderMarkdown(text);
  addCopyActions(msgEl, bubbleEl, metaEl);
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
    const { msgEl, bubbleEl, metaEl } = createMsgBubble("ai");
    state._aiMsgEl    = msgEl;
    state._aiBubbleEl = bubbleEl;
    bubbleEl.classList.add("markdown-body");
    addCopyActions(msgEl, bubbleEl, metaEl);
    els.messages.appendChild(msgEl);
  }
  state._aiBubbleEl._raw = (state._aiBubbleEl._raw || "") + text;
  state._aiBubbleEl.innerHTML = renderMarkdown(state._aiBubbleEl._raw);
  pinThinkingMsgToBottom();
  scrollToBottom();
}

export function finalizeAiMsg() {
  removeThinkingMsg();
  if (state._aiMsgEl && !state._aiBubbleEl?._raw?.trim()) {
    state._aiMsgEl.remove();
  }
  state._aiMsgEl    = null;
  state._aiBubbleEl = null;
}

// ── Tool call / result bubbles ─────────────────────────────────────────────

export function insertToolBubble(data) {
  if (state._aiMsgEl && !state._aiBubbleEl?._raw?.trim()) {
    state._aiMsgEl.remove();
  }
  state._aiMsgEl    = null;
  state._aiBubbleEl = null;

  const el = document.createElement("div");
  el.className = "tool-line";
  el.innerHTML = `<span class="tl-name">⚙ ${escHtml(data.tool || "tool")}</span>`
               + `<span class="tl-status">running…</span>`;
  els.messages.appendChild(el);

  if (data.call_id) {
    state._toolBubbles[data.call_id] = el;
  } else if (data.tool) {
    if (!state._toolPendingByName[data.tool]) state._toolPendingByName[data.tool] = [];
    state._toolPendingByName[data.tool].push(el);
  }
  state._toolIndex++;
  pinThinkingMsgToBottom();
  scrollToBottom();
}

export function updateToolBubble(data) {
  let el = null;
  if (data.call_id && state._toolBubbles[data.call_id]) {
    el = state._toolBubbles[data.call_id];
  } else if (data.tool && state._toolPendingByName[data.tool]?.length) {
    el = state._toolPendingByName[data.tool].shift();
  }
  if (!el) return;

  const raw = typeof data.result === "string" ? data.result : prettyJson(data.result);
  renderToolResult(el, data.tool || "tool", raw);
}

export function renderToolResult(el, toolName, raw) {
  const preview = raw.replace(/\s+/g, " ").trim().slice(0, 100);
  const expandable = raw.length > 100 || raw.includes("\n");
  el.className = "tool-line has-result";
  el.innerHTML = `<span class="tl-name">⚙ ${escHtml(toolName)}</span>`
               + `<span class="tl-result">${escHtml(preview)}</span>`
               + `<span class="tl-done">✓</span>`
               + (expandable ? `<span class="tl-expand">›</span><div class="tl-body">${escHtml(raw)}</div>` : "");
  if (expandable) {
    el.addEventListener("click", () => {
      el.classList.toggle("expanded");
    });
  }
}

// ── Thinking indicator ─────────────────────────────────────────────────────

export function insertThinkingMsg() {
  removeThinkingMsg();
  const { msgEl, bubbleEl } = createMsgBubble("ai");
  bubbleEl.classList.add("thinking-bubble");
  bubbleEl.textContent = "⠋ thinking…";
  els.messages.appendChild(msgEl);
  scrollToBottom();
  state._thinkingEl    = msgEl;
  state._thinkingStart = Date.now();
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

// Markdown rendering is implemented in modules/markdown.js

// ── Session history restore ────────────────────────────────────────────────

export function renderSessionHistory(session_id, turns) {
  removeThinkingMsg();
  els.messages.innerHTML = "";
  state._aiMsgEl     = null;
  state._aiBubbleEl  = null;
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex   = 0;

  state.session_id = session_id;
  els.sessionLabel.textContent = `session: ${session_id}`;

  if (!turns.length) {
    insertSystemMsg("No history for this session.");
    return;
  }

  for (const t of turns) {
    if (t.role === "user") {
      insertUserMsg(t.content || "");
    } else if (t.role === "assistant") {
      const { msgEl, bubbleEl, metaEl } = createMsgBubble("ai");
      bubbleEl.classList.add("markdown-body");
      bubbleEl._raw = t.content || "";
      bubbleEl.innerHTML = renderMarkdown(bubbleEl._raw);
      addCopyActions(msgEl, bubbleEl, metaEl);
      els.messages.appendChild(msgEl);
    } else if (t.role === "tool") {
      const el = document.createElement("div");
      renderToolResult(el, t.tool_name || "tool", t.content || "");
      els.messages.appendChild(el);
    }
  }
  scrollToBottom();
}

// ── New session ────────────────────────────────────────────────────────────

export function newSession() {
  removeThinkingMsg();
  state.session_id = null;
  state._activeSessionId = null;
  state.inTokens   = 0;
  state.outTokens  = 0;
  state._toolBubbles = {};
  state._toolPendingByName = {};
  state._toolIndex   = 0;
  state._aiMsgEl     = null;
  state._aiBubbleEl  = null;
  els.messages.innerHTML = "";
  els.sessionLabel.textContent = "session: —";
  els.tokenStats.textContent   = "";
  document.querySelectorAll(".sidebar-session").forEach((el) => el.classList.remove("active"));
  insertSystemMsg("New session started. Use this when you switch to a new topic.");
}
