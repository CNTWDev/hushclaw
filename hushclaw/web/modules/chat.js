/**
 * chat.js — Chat message rendering, markdown, thinking indicator, session history.
 */

import { state, els, SPINNERS, escHtml, prettyJson } from "./state.js";

let _spinIdx = 0;

// ── Scrolling ──────────────────────────────────────────────────────────────

export function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

// ── Message bubble factory ─────────────────────────────────────────────────

export function createMsgBubble(kind) {
  const msgEl = document.createElement("div");
  msgEl.className = `msg ${kind}`;
  const bubbleEl = document.createElement("div");
  bubbleEl.className = "bubble";
  msgEl.appendChild(bubbleEl);
  return { msgEl, bubbleEl };
}

function addCopyButton(msgEl, bubbleEl) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "msg-copy-btn";
  btn.textContent = "Copy";
  btn.title = "Copy original markdown";
  btn.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    const raw = bubbleEl._raw ?? bubbleEl.textContent ?? "";
    try {
      await navigator.clipboard.writeText(raw);
      btn.textContent = "Copied";
      setTimeout(() => { btn.textContent = "Copy"; }, 1200);
    } catch {
      btn.textContent = "Failed";
      setTimeout(() => { btn.textContent = "Copy"; }, 1200);
    }
  });
  msgEl.appendChild(btn);
}

// ── Chat message helpers ───────────────────────────────────────────────────

export function insertUserMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("user");
  bubbleEl._raw = text;
  bubbleEl.innerHTML = renderMarkdown(text);
  addCopyButton(msgEl, bubbleEl);
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
    const { msgEl, bubbleEl } = createMsgBubble("ai");
    state._aiMsgEl    = msgEl;
    state._aiBubbleEl = bubbleEl;
    addCopyButton(msgEl, bubbleEl);
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

// ── Markdown (minimal, XSS-safe) ──────────────────────────────────────────

export function renderMarkdown(raw) {
  let s = escHtml(String(raw).replace(/\r\n?/g, "\n"));

  // Preserve fenced code blocks before other markdown replacements.
  const fenced = [];
  s = s.replace(/```([\w-]*)\n([\s\S]*?)```/g, (_m, lang, inner) => {
    const i = fenced.length;
    const cls = lang ? ` class="lang-${lang}"` : "";
    fenced.push(`<pre><code${cls}>${inner}</code></pre>`);
    return `@@FENCED_${i}@@`;
  });

  // Preserve inline code before strong/em parsing.
  const inlineCodes = [];
  s = s.replace(/`([^`]+)`/g, (_m, inner) => {
    const i = inlineCodes.length;
    inlineCodes.push(`<code>${inner}</code>`);
    return `@@INLINE_${i}@@`;
  });

  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_m, label, href) => {
    return `<a href="${href}" target="_blank" rel="noopener">${label}</a>`;
  });

  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");

  // Lightweight headings and quotes.
  s = s.replace(/^######[ \t]+(.+)$/gm, "<h6>$1</h6>");
  s = s.replace(/^#####[ \t]+(.+)$/gm, "<h5>$1</h5>");
  s = s.replace(/^####[ \t]+(.+)$/gm, "<h4>$1</h4>");
  s = s.replace(/^###[ \t]+(.+)$/gm, "<h3>$1</h3>");
  s = s.replace(/^##[ \t]+(.+)$/gm, "<h2>$1</h2>");
  s = s.replace(/^#[ \t]+(.+)$/gm, "<h1>$1</h1>");
  s = s.replace(/^>[ \t]?(.+)$/gm, "<blockquote>$1</blockquote>");

  s = s.replace(/\/files\/([\w.\-]+)/g, (_, fid) => {
    const apiKey = new URLSearchParams(location.search).get("api_key") || "";
    const href = apiKey ? `/files/${fid}?api_key=${encodeURIComponent(apiKey)}` : `/files/${fid}`;
    const name = fid.includes("_") ? fid.split("_").slice(1).join("_") : fid;
    return `<a class="dl-link" href="${href}" download="${escHtml(name)}">⬇ ${escHtml(name)}</a>`;
  });

  s = s.replace(/@@INLINE_(\d+)@@/g, (_m, i) => inlineCodes[Number(i)] || "");
  s = s.replace(/@@FENCED_(\d+)@@/g, (_m, i) => fenced[Number(i)] || "");
  return s;
}

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
      const { msgEl, bubbleEl } = createMsgBubble("ai");
      bubbleEl._raw = t.content || "";
      bubbleEl.innerHTML = renderMarkdown(bubbleEl._raw);
      addCopyButton(msgEl, bubbleEl);
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
  insertSystemMsg("New session started.");
}
