/**
 * GhostClaw Web UI — app.js
 * Pure JS, no build step, no external dependencies.
 */

"use strict";

// ── State ──────────────────────────────────────────────────────────────────

const state = {
  ws: null,
  session_id: null,
  agent: "default",
  tab: "chat",
  inTokens: 0,
  outTokens: 0,
  sending: false,
  // reconnect
  _reconnectDelay: 1000,
  _reconnectTimer: null,
  // tool bubbles map: call_index → element
  _toolBubbles: {},
  _toolIndex: 0,
  // current streaming AI bubble
  _aiMsgEl: null,
  _aiBubbleEl: null,
};

// ── DOM refs ───────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

const els = {
  agentSelect:      $("agent-select"),
  messages:         $("messages"),
  input:            $("input"),
  btnSend:          $("btn-send"),
  btnNew:           $("btn-new-session"),
  sessionLabel:     $("session-label"),
  connStatus:       $("conn-status"),
  tokenStats:       $("token-stats"),
  sessionsList:     $("sessions-list"),
  memoriesList:     $("memories-list"),
  memorySearch:     $("memory-search"),
  btnSearchMem:     $("btn-search-memories"),
  btnRefreshMem:    $("btn-refresh-memories"),
  btnRefreshSess:   $("btn-refresh-sessions"),
};

// ── WebSocket ──────────────────────────────────────────────────────────────

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams(location.search);
  const key = params.get("api_key") || "";
  const q = key ? `?api_key=${encodeURIComponent(key)}` : "";
  return `${proto}//${location.host}${q}`;
}

function connect() {
  if (state.ws && state.ws.readyState <= WebSocket.OPEN) return;

  setConnStatus("reconnecting");
  const ws = new WebSocket(wsUrl());
  state.ws = ws;

  ws.onopen = () => {
    setConnStatus("connected");
    state._reconnectDelay = 1000;
    els.btnSend.disabled = false;
    // fetch agents list
    send({ type: "list_agents" });
  };

  ws.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch { return; }
    handleMessage(data);
  };

  ws.onclose = () => {
    setConnStatus("disconnected");
    els.btnSend.disabled = true;
    scheduleReconnect();
  };

  ws.onerror = () => {
    ws.close();
  };
}

function scheduleReconnect() {
  if (state._reconnectTimer) return;
  const delay = state._reconnectDelay;
  state._reconnectDelay = Math.min(delay * 2, 30000);
  state._reconnectTimer = setTimeout(() => {
    state._reconnectTimer = null;
    connect();
  }, delay);
}

function send(obj) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(obj));
  }
}

// ── Message dispatcher ────────────────────────────────────────────────────

function handleMessage(data) {
  switch (data.type) {
    case "session":
      state.session_id = data.session_id;
      els.sessionLabel.textContent = `session: ${data.session_id}`;
      break;

    case "chunk":
      appendChunk(data.text || "");
      break;

    case "tool_call":
      insertToolBubble(data);
      break;

    case "tool_result":
      updateToolBubble(data);
      break;

    case "compaction":
      insertSystemMsg(`Context compacted — archived ${data.archived} turns, kept ${data.kept}.`);
      break;

    case "done":
      finalizeAiMsg();
      state.inTokens  += data.input_tokens  || 0;
      state.outTokens += data.output_tokens || 0;
      updateTokenStats();
      setSending(false);
      break;

    case "error":
      finalizeAiMsg();
      insertErrorMsg(data.message || "Unknown error");
      setSending(false);
      break;

    case "agents":
      populateAgents(data.items || []);
      break;

    case "sessions":
      renderSessions(data.items || []);
      break;

    case "memories":
      renderMemories(data.items || []);
      break;

    case "memory_deleted":
      onMemoryDeleted(data.note_id, data.ok);
      break;

    case "pipeline_step":
      insertSystemMsg(`Pipeline step [${data.agent}]: ${data.output || ""}`);
      break;

    case "pong":
      break;
  }
}

// ── Chat rendering ────────────────────────────────────────────────────────

function appendChunk(text) {
  if (!state._aiMsgEl) {
    const { msgEl, bubbleEl } = createMsgBubble("ai");
    state._aiMsgEl   = msgEl;
    state._aiBubbleEl = bubbleEl;
    els.messages.appendChild(msgEl);
  }
  // accumulate raw text, render inline markdown
  state._aiBubbleEl._raw = (state._aiBubbleEl._raw || "") + text;
  state._aiBubbleEl.innerHTML = renderMarkdown(state._aiBubbleEl._raw);
  scrollToBottom();
}

function finalizeAiMsg() {
  state._aiMsgEl   = null;
  state._aiBubbleEl = null;
  state._toolBubbles = {};
  state._toolIndex   = 0;
}

function insertUserMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("user");
  bubbleEl.textContent = text;
  els.messages.appendChild(msgEl);
  scrollToBottom();
}

function insertSystemMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("system");
  bubbleEl.textContent = text;
  els.messages.appendChild(msgEl);
  scrollToBottom();
}

function insertErrorMsg(text) {
  const { msgEl, bubbleEl } = createMsgBubble("error");
  bubbleEl.textContent = "Error: " + text;
  els.messages.appendChild(msgEl);
  scrollToBottom();
}

function createMsgBubble(kind) {
  const msgEl = document.createElement("div");
  msgEl.className = `msg ${kind}`;
  const bubbleEl = document.createElement("div");
  bubbleEl.className = "bubble";
  msgEl.appendChild(bubbleEl);
  return { msgEl, bubbleEl };
}

function insertToolBubble(data) {
  const idx = state._toolIndex++;
  const wrapper = document.createElement("div");
  wrapper.className = "tool-bubble";
  wrapper.dataset.idx = idx;

  const header = document.createElement("div");
  header.className = "tool-header";
  header.innerHTML = `
    <span class="tool-arrow">▶</span>
    <span class="tool-name">→ ${escHtml(data.tool || "tool")}</span>
    <span class="tool-meta" style="color:var(--muted);font-size:11px;margin-left:auto"></span>
  `;
  header.addEventListener("click", () => {
    wrapper.classList.toggle("open");
  });

  const body = document.createElement("div");
  body.className = "tool-body";
  body.innerHTML = `
    <div class="tool-section-label">INPUT</div>
    <pre class="tool-pre">${escHtml(prettyJson(data.input))}</pre>
    <div class="tool-section-label result-label" style="display:none">RESULT</div>
    <pre class="tool-pre result-pre" style="display:none"></pre>
  `;

  wrapper.appendChild(header);
  wrapper.appendChild(body);
  els.messages.appendChild(wrapper);
  state._toolBubbles[data.tool + "_" + idx] = wrapper;
  // Store by tool name for result matching (simple: last tool with that name)
  state._toolBubbles["__last_" + data.tool] = wrapper;
  scrollToBottom();
}

function updateToolBubble(data) {
  const bubble = state._toolBubbles["__last_" + data.tool];
  if (!bubble) return;
  const resultLabel = bubble.querySelector(".result-label");
  const resultPre   = bubble.querySelector(".result-pre");
  if (resultLabel && resultPre) {
    resultLabel.style.display = "";
    resultPre.style.display   = "";
    resultPre.textContent = typeof data.result === "string"
      ? data.result
      : prettyJson(data.result);
    const meta = bubble.querySelector(".tool-meta");
    if (meta) meta.textContent = "✓";
  }
}

// ── Markdown (minimal) ────────────────────────────────────────────────────

function renderMarkdown(raw) {
  // Escape HTML first, then apply minimal markdown
  let s = escHtml(raw);
  // ```code blocks```
  s = s.replace(/```[\s\S]*?```/g, (m) => {
    const inner = m.slice(3, -3).replace(/^\w*\n/, "");
    return `<code>${inner}</code>`;
  });
  // `inline code`
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  // **bold**
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  // *italic*
  s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  return s;
}

// ── Agents ────────────────────────────────────────────────────────────────

function populateAgents(items) {
  els.agentSelect.innerHTML = "";
  if (!items.length) {
    const opt = document.createElement("option");
    opt.value = "default";
    opt.textContent = "default";
    els.agentSelect.appendChild(opt);
    return;
  }
  items.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.name;
    opt.textContent = a.name + (a.description ? ` — ${a.description}` : "");
    if (a.name === state.agent) opt.selected = true;
    els.agentSelect.appendChild(opt);
  });
}

// ── Sessions panel ────────────────────────────────────────────────────────

function renderSessions(items) {
  els.sessionsList.innerHTML = "";
  if (!items.length) {
    els.sessionsList.innerHTML = '<div class="empty-state">No sessions found.</div>';
    return;
  }
  items.forEach((s) => {
    const el = document.createElement("div");
    el.className = "list-item";
    const inTok  = (s.total_input_tokens  || 0).toLocaleString();
    const outTok = (s.total_output_tokens || 0).toLocaleString();
    el.innerHTML = `
      <div class="list-item-title">${escHtml(s.session_id || "—")}</div>
      <div class="list-item-meta">
        Turns: ${s.turn_count || 0} &nbsp;|&nbsp;
        In: ${inTok} &nbsp;|&nbsp; Out: ${outTok}
      </div>
      ${s.last_turn ? `<div class="list-item-meta">Last: ${escHtml(String(s.last_turn))}</div>` : ""}
    `;
    // click to resume this session in chat
    el.style.cursor = "pointer";
    el.addEventListener("click", () => {
      state.session_id = s.session_id;
      els.sessionLabel.textContent = `session: ${s.session_id}`;
      switchTab("chat");
    });
    els.sessionsList.appendChild(el);
  });
}

// ── Memories panel ────────────────────────────────────────────────────────

function renderMemories(items) {
  els.memoriesList.innerHTML = "";
  if (!items.length) {
    els.memoriesList.innerHTML = '<div class="empty-state">No memories found.</div>';
    return;
  }
  items.forEach((m) => {
    const el = document.createElement("div");
    el.className = "list-item";
    el.dataset.noteId = m.id || m.note_id || "";
    const tags   = (m.tags || []).join(", ") || "—";
    const score  = m.score != null ? ` &nbsp;|&nbsp; score: ${m.score.toFixed(2)}` : "";
    el.innerHTML = `
      <div class="list-item-title">${escHtml(m.content || m.text || "")}</div>
      <div class="list-item-meta">
        ID: ${escHtml(el.dataset.noteId)} &nbsp;|&nbsp; tags: ${escHtml(tags)}${score}
      </div>
      <div class="list-item-actions">
        <button class="danger" data-note-id="${escHtml(el.dataset.noteId)}">Delete</button>
      </div>
    `;
    el.querySelector(".danger").addEventListener("click", (ev) => {
      ev.stopPropagation();
      const noteId = ev.target.dataset.noteId;
      if (!noteId) return;
      if (!confirm(`Delete memory ${noteId}?`)) return;
      send({ type: "delete_memory", note_id: noteId });
    });
    els.memoriesList.appendChild(el);
  });
}

function onMemoryDeleted(noteId, ok) {
  if (!ok) {
    alert(`Failed to delete memory: ${noteId}`);
    return;
  }
  const el = els.memoriesList.querySelector(`[data-note-id="${CSS.escape(noteId)}"]`);
  if (el) el.remove();
  if (!els.memoriesList.children.length) {
    els.memoriesList.innerHTML = '<div class="empty-state">No memories found.</div>';
  }
}

// ── UI helpers ─────────────────────────────────────────────────────────────

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `panel-${tab}`);
  });
  if (tab === "sessions") send({ type: "list_sessions" });
  if (tab === "memories") send({ type: "list_memories", limit: 20 });
}

function setConnStatus(status) {
  els.connStatus.className = `dot ${status}`;
  els.connStatus.title = status.charAt(0).toUpperCase() + status.slice(1);
}

function updateTokenStats() {
  if (state.inTokens || state.outTokens) {
    els.tokenStats.textContent =
      `In: ${state.inTokens.toLocaleString()}  Out: ${state.outTokens.toLocaleString()}`;
  }
}

function setSending(v) {
  state.sending = v;
  els.btnSend.disabled = v || !state.ws || state.ws.readyState !== WebSocket.OPEN;
  els.input.disabled = v;
}

function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function prettyJson(v) {
  if (v == null) return "";
  if (typeof v === "string") return v;
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

function newSession() {
  state.session_id = null;
  state.inTokens   = 0;
  state.outTokens  = 0;
  state._toolBubbles = {};
  state._toolIndex   = 0;
  state._aiMsgEl     = null;
  state._aiBubbleEl  = null;
  els.messages.innerHTML = "";
  els.sessionLabel.textContent = "session: —";
  els.tokenStats.textContent   = "";
  insertSystemMsg("New session started.");
}

// ── Auto-resize textarea ───────────────────────────────────────────────────

function autoResize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 120) + "px";
}

// ── Event listeners ────────────────────────────────────────────────────────

function sendMessage() {
  const text = els.input.value.trim();
  if (!text || state.sending) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;

  insertUserMsg(text);
  els.input.value = "";
  autoResize();

  setSending(true);

  const payload = {
    type:       "chat",
    text,
    agent:      state.agent,
    session_id: state.session_id || undefined,
  };
  send(payload);
}

els.btnSend.addEventListener("click", sendMessage);

els.input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    sendMessage();
  }
});

els.input.addEventListener("input", autoResize);

els.btnNew.addEventListener("click", newSession);

els.agentSelect.addEventListener("change", () => {
  state.agent = els.agentSelect.value;
});

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

els.btnRefreshSess.addEventListener("click", () => {
  send({ type: "list_sessions" });
});

els.btnRefreshMem.addEventListener("click", () => {
  els.memorySearch.value = "";
  send({ type: "list_memories", limit: 20 });
});

els.btnSearchMem.addEventListener("click", () => {
  const q = els.memorySearch.value.trim();
  send({ type: "list_memories", query: q, limit: 20 });
});

els.memorySearch.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    const q = els.memorySearch.value.trim();
    send({ type: "list_memories", query: q, limit: 20 });
  }
});

// ── Boot ──────────────────────────────────────────────────────────────────

insertSystemMsg("Connecting to GhostClaw…");
connect();
