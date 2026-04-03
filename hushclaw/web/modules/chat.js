/**
 * chat.js — Chat message rendering, markdown, thinking indicator, session history.
 */

import {
  state, els, SPINNERS, escHtml, prettyJson, showToast,
  isSessionRunning, setCurrentSessionId, clearCurrentSessionId, debugUiLifecycle,
} from "./state.js";
import { renderMarkdown } from "./markdown.js";

let _spinIdx = 0;
const COPY_IMAGE_WATERMARK = "HushClaw Powered by TEX AI@Transsion";
const HTML2CANVAS_URL = "https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js";
const JSPDF_URL = "https://cdn.jsdelivr.net/npm/jspdf@2.5.1/dist/jspdf.umd.min.js";
let _html2canvasLoading = null;
let _jsPdfLoading = null;

// ── Scrolling ──────────────────────────────────────────────────────────────

export function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
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

async function ensureJsPdf() {
  if (window.jspdf?.jsPDF) return window.jspdf.jsPDF;
  if (_jsPdfLoading) return _jsPdfLoading;
  _jsPdfLoading = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = JSPDF_URL;
    s.async = true;
    s.onload = () => {
      const ctor = window.jspdf?.jsPDF;
      if (ctor) resolve(ctor);
      else reject(new Error("jsPDF loaded but unavailable"));
    };
    s.onerror = () => reject(new Error("Failed to load jsPDF"));
    document.head.appendChild(s);
  });
  return _jsPdfLoading;
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

function fmtTime(d) {
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hhmm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  if (sameDay) return hhmm;
  const mo = (d.getMonth() + 1).toString().padStart(2, "0");
  const dd = d.getDate().toString().padStart(2, "0");
  return `${mo}-${dd} ${hhmm}`;
}

function _fmtPdfStamp(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${y}${m}${day}-${hh}${mm}`;
}

function _roleLabelFromMsg(msgEl) {
  if (msgEl.classList.contains("user")) return "You";
  if (msgEl.classList.contains("ai")) return "Assistant";
  if (msgEl.classList.contains("system")) return "System";
  if (msgEl.classList.contains("error")) return "Error";
  return "Message";
}

function _normalizePdfText(s) {
  return String(s || "")
    .replace(/\r\n/g, "\n")
    .replace(/\u00A0/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function _addPdfParagraph(doc, lines, y, cfg) {
  for (const line of lines) {
    if (y + cfg.lineHeight > cfg.pageHeight - cfg.marginBottom) {
      doc.addPage();
      y = cfg.marginTop;
    }
    doc.text(line || " ", cfg.marginLeft, y);
    y += cfg.lineHeight;
  }
  return y;
}

async function _buildTextPdf(blocks, title = "HushClaw Chat Export") {
  const jsPDF = await ensureJsPdf();
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const pageWidth = doc.internal.pageSize.getWidth();
  const pageHeight = doc.internal.pageSize.getHeight();
  const cfg = {
    marginLeft: 42,
    marginRight: 42,
    marginTop: 48,
    marginBottom: 42,
    lineHeight: 14,
    pageHeight,
  };
  const maxTextWidth = pageWidth - cfg.marginLeft - cfg.marginRight;

  let y = cfg.marginTop;
  doc.setFont("helvetica", "bold");
  doc.setFontSize(14);
  y = _addPdfParagraph(doc, doc.splitTextToSize(title, maxTextWidth), y, cfg);
  y += 4;
  doc.setFont("helvetica", "normal");
  doc.setFontSize(10.5);
  y = _addPdfParagraph(
    doc,
    [`Generated at ${new Date().toLocaleString()}`, " "],
    y,
    cfg,
  );

  for (const block of blocks) {
    const head = `[${block.time || "--:--"}] ${block.role || "Message"}`;
    doc.setFont("helvetica", "bold");
    doc.setFontSize(11.5);
    y = _addPdfParagraph(doc, doc.splitTextToSize(head, maxTextWidth), y, cfg);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(10.5);
    const text = _normalizePdfText(block.text || "");
    const bodyLines = doc.splitTextToSize(text || "(empty)", maxTextWidth);
    y = _addPdfParagraph(doc, bodyLines, y, cfg);
    y = _addPdfParagraph(doc, [" "], y, cfg);
  }
  return doc;
}

async function _exportSingleMessagePdf(msgEl, bubbleEl, btn) {
  const role = _roleLabelFromMsg(msgEl);
  const time = msgEl.querySelector(".msg-time")?.textContent?.trim() || fmtTime(new Date());
  const text = bubbleEl?._raw ?? bubbleEl?.textContent ?? "";
  const doc = await _buildTextPdf([{ role, time, text }], `${role} Message`);
  doc.save(`hushclaw-message-${_fmtPdfStamp()}.pdf`);
  setCopyBtnTempText(btn, "Saved", "PDF");
}

function addCopyActions(msgEl, bubbleEl, contentEl, ts) {
  const footer = document.createElement("div");
  footer.className = "msg-actions-footer";

  const actions = document.createElement("div");
  actions.className = "msg-copy-actions";

  const timeEl = document.createElement("span");
  timeEl.className = "msg-time";
  timeEl.textContent = fmtTime(ts instanceof Date ? ts : new Date());
  actions.appendChild(timeEl);

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

  const pdfBtn = document.createElement("button");
  pdfBtn.type = "button";
  pdfBtn.className = "msg-copy-btn";
  pdfBtn.textContent = "PDF";
  pdfBtn.title = "Export this message as PDF";
  pdfBtn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    try {
      await _exportSingleMessagePdf(msgEl, bubbleEl, pdfBtn);
    } catch (err) {
      setCopyBtnTempText(pdfBtn, "Failed", "PDF");
      showToast(`Export PDF failed: ${String(err?.message || err || "unknown error")}`, "error");
    }
  });

  actions.appendChild(mdBtn);
  actions.appendChild(imgBtn);
  actions.appendChild(pdfBtn);
  footer.appendChild(timeEl);
  footer.appendChild(actions);
  contentEl.appendChild(footer);
}

function _collectSessionPdfBlocks() {
  const blocks = [];
  const msgEls = Array.from(els.messages.querySelectorAll(".msg"));
  for (const msgEl of msgEls) {
    const bubbleEl = msgEl.querySelector(".bubble");
    if (!bubbleEl) continue;
    if (bubbleEl.classList.contains("thinking-bubble")) continue;
    const text = _normalizePdfText(bubbleEl._raw ?? bubbleEl.textContent ?? "");
    if (!text) continue;
    blocks.push({
      role: _roleLabelFromMsg(msgEl),
      time: msgEl.querySelector(".msg-time")?.textContent?.trim() || "",
      text,
    });
  }
  return blocks;
}

export async function exportCurrentSessionAsPdf(btn = null) {
  const blocks = _collectSessionPdfBlocks();
  if (!blocks.length) {
    showToast("No chat messages to export yet.", "warn");
    return;
  }
  const prev = btn?.textContent || "";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Exporting…";
  }
  try {
    const doc = await _buildTextPdf(blocks, "HushClaw Chat Export");
    doc.save(`hushclaw-chat-${_fmtPdfStamp()}.pdf`);
    showToast("Chat PDF exported.", "ok");
  } catch (err) {
    showToast(`Export chat PDF failed: ${String(err?.message || err || "unknown error")}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = prev || "Export PDF";
    }
  }
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
  }
  state._aiBubbleEl._raw = (state._aiBubbleEl._raw || "") + text;
  state._aiBubbleEl.innerHTML = renderMarkdown(state._aiBubbleEl._raw);
  pinThinkingMsgToBottom();
  scrollToBottom();
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
  renderToolResult(el, data.tool || "tool", raw, !!data.is_error);
}

export function renderToolResult(el, toolName, raw, isError = false) {
  const preview = raw.replace(/\s+/g, " ").trim().slice(0, 100);
  const expandable = raw.length > 100 || raw.includes("\n");
  el.className = isError ? "tool-line has-error" : "tool-line has-result";
  const statusIcon = isError
    ? `<span class="tl-err">✗</span>`
    : `<span class="tl-done">✓</span>`;
  el.innerHTML = `<span class="tl-name">⚙ ${escHtml(toolName)}</span>`
               + `<span class="tl-result">${escHtml(preview)}</span>`
               + statusIcon
               + (expandable ? `<span class="tl-expand">›</span><div class="tl-body">${escHtml(raw)}</div>` : "");
  if (expandable) {
    el.addEventListener("click", () => {
      el.classList.toggle("expanded");
    });
  }
}

export function insertRoundLine(round, maxRounds) {
  const el = document.createElement("div");
  el.className = "round-line";
  const maxStr = maxRounds > 0 ? `/${maxRounds}` : "";
  el.textContent = `↺  round ${round}${maxStr}`;
  els.messages.appendChild(el);
  scrollToBottom();
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
  // Marker already exists (tool bubbles) — just correct _thinkingStart so the
  // existing timer (if any) reflects the real session start time.
  state._thinkingStart = startedAt;
  pinThinkingMsgToBottom();
  scrollToBottom();
}

// Markdown rendering is implemented in modules/markdown.js

// ── Session history restore ────────────────────────────────────────────────

export function renderSessionHistory(session_id, turns) {
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

  setCurrentSessionId(session_id);

  if (!turns.length) {
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
  els.messages.innerHTML = "";
  els.tokenStats.textContent   = "";
  document.querySelectorAll(".sidebar-session").forEach((el) => el.classList.remove("active"));
}
