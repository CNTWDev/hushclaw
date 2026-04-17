/**
 * panels/sessions.js — Sessions sidebar, workspace selector, memories panel.
 */

import {
  state, els, learning, send, sendListMemories, escHtml, showToast,
  getCurrentSessionId, setCurrentSessionId, clearCurrentSessionId,
} from "../state.js";
import { resetChatSessionUiState } from "../chat.js";
import { openConfirm, openDialog, closeModal } from "../modal.js";

// ── Memories pagination state ─────────────────────────────────────────────
let _memQuery = "";
let _memIncludeAuto = false;
let _memOffset = 0;
let _memKinds = ["user_model", "project_knowledge", "decision"];
let _sessionQuery = "";

const SESSIONS_COLLAPSED_KEY = "hushclaw.ui.sessions-collapsed";
let _sessionsCollapsed = false;

// ── Sessions sidebar ──────────────────────────────────────────────────────

export function loadSession(session_id) {
  setCurrentSessionId(session_id);
  document.querySelectorAll(".sidebar-session").forEach((el) => {
    el.classList.toggle("active", el.dataset.sessionId === session_id);
  });
  send({ type: "get_session_history", session_id });
}

export function renderSessions(items) {
  const list = document.getElementById("sessions-list");
  if (!list) return;
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<div class="empty-state" style="padding:12px;font-size:11px">No sessions</div>';
    state._firstSessionLoad = false;
    return;
  }

  items.forEach((s) => {
    const el = document.createElement("div");
    el.className = "sidebar-session" + (s.session_id === getCurrentSessionId() ? " active" : "");
    el.dataset.sessionId = s.session_id;

    const shortId = (s.session_id || "—").slice(-12);
    const title = (s.title || "").trim() || `Session ${shortId}`;
    const lastPreview = (s.last_preview || "").trim();
    const kind = s.kind || "chat";
    const kindLabel = kind === "scheduled" ? "SCHED" : (kind === "auto" ? "AUTO" : (kind === "broadcast" ? "CAST" : ""));
    const source = (s.source || "").trim();
    const sourceLabel = source && source !== "event_stream" && source !== "run"
      ? source.replaceAll("_", " ").toUpperCase()
      : "";
    const compactCount = Number(s.compaction_count || 0);
    const lastTs = s.last_turn
      ? new Date(s.last_turn * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
      : "";
    const metaExtras = [
      compactCount > 0 ? `${compactCount} compact` : "",
      sourceLabel,
    ].filter(Boolean).join(" · ");

    el.innerHTML = `
      <div class="sidebar-session-info">
        <div class="sidebar-session-title-row">
          <div class="sidebar-session-title" title="${escHtml(title)}">${escHtml(title)}</div>
          ${kindLabel ? `<span class="session-kind-badge">${kindLabel}</span>` : ""}
        </div>
        <div class="sidebar-session-meta">${s.turn_count || 0} turns${lastTs ? " · " + lastTs : ""}${metaExtras ? " · " + escHtml(metaExtras) : ""} · ${escHtml(shortId)}</div>
        ${lastPreview ? `<div class="sidebar-session-preview">${escHtml(lastPreview)}</div>` : ""}
      </div>
      <button class="session-delete-btn" data-session-id="${escHtml(s.session_id || "")}" title="Delete session">✕</button>
    `;
    el.querySelector(".session-delete-btn").addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const sid = ev.currentTarget.dataset.sessionId;
      if (!sid) return;
      const confirmed = await openConfirm({
        title: "Delete session",
        message: `Delete this session (${sid.slice(-12)})? Chat history for it will be removed.`,
        confirmText: "Delete",
        cancelText: "Cancel",
        dangerConfirm: true,
      });
      if (!confirmed) return;
      send({ type: "delete_session", session_id: sid });
    });
    el.addEventListener("click", () => loadSession(s.session_id));
    list.appendChild(el);
  });
  state._firstSessionLoad = false;
}

function _mapSearchResultsToSessions(items) {
  const seen = new Set();
  const out = [];
  for (const item of items || []) {
    const sessionId = String(item.session_id || "").trim();
    if (!sessionId || seen.has(sessionId)) continue;
    seen.add(sessionId);
    out.push({
      session_id: sessionId,
      title: item.title || "",
      last_preview: item.snippet || item.content || "",
      turn_count: "",
      kind: item.kind || "chat",
      last_turn: item.ts || 0,
      source: item.source || "",
      parent_session_id: item.parent_session_id || "",
      compaction_count: item.compaction_count || 0,
    });
  }
  return out;
}

export function renderSessionSearchResults(items, query = "") {
  _sessionQuery = (query || "").trim();
  if (els.sessionSearch) els.sessionSearch.value = _sessionQuery;
  renderSessions(_mapSearchResultsToSessions(items));
}

export function selectedMemoryKinds() {
  const value = String(document.getElementById("mem-kind-filter")?.value || "visible");
  if (value === "all") return ["all"];
  if (["user_model", "project_knowledge", "decision", "session_memory", "telemetry"].includes(value)) {
    return [value];
  }
  return ["user_model", "project_knowledge", "decision"];
}

export function refreshSessionsView() {
  if (_sessionQuery) {
    send({
      type: "search_sessions",
      query: _sessionQuery,
      workspace: state.activeWorkspace || "",
    });
    return;
  }
  send({ type: "list_sessions", workspace: state.activeWorkspace || "" });
}

export function runSessionSearch(query) {
  _sessionQuery = (query || "").trim();
  if (!_sessionQuery) {
    clearSessionSearch();
    return;
  }
  send({
    type: "search_sessions",
    query: _sessionQuery,
    workspace: state.activeWorkspace || "",
  });
}

export function clearSessionSearch() {
  _sessionQuery = "";
  if (els.sessionSearch) els.sessionSearch.value = "";
  send({ type: "list_sessions", workspace: state.activeWorkspace || "" });
}

function _applySessionsCollapsed(collapsed) {
  _sessionsCollapsed = !!collapsed;
  document.body.classList.toggle("sessions-collapsed", _sessionsCollapsed);
  if (els.btnToggleSess) {
    els.btnToggleSess.textContent = _sessionsCollapsed ? "⟩" : "⟨";
    els.btnToggleSess.title = _sessionsCollapsed ? "Expand sessions" : "Collapse sessions";
  }
  if (els.btnToggleSessInline) {
    els.btnToggleSessInline.classList.toggle("hidden", !_sessionsCollapsed);
  }
  try { localStorage.setItem(SESSIONS_COLLAPSED_KEY, _sessionsCollapsed ? "1" : "0"); } catch {}
}

export function toggleSessionsSidebar(forceCollapsed) {
  if (typeof forceCollapsed === "boolean") {
    _applySessionsCollapsed(forceCollapsed);
    return;
  }
  _applySessionsCollapsed(!_sessionsCollapsed);
}

export function initSessionsSidebarState() {
  let collapsed = false;
  try { collapsed = localStorage.getItem(SESSIONS_COLLAPSED_KEY) === "1"; } catch {}
  _applySessionsCollapsed(collapsed);
}

export function onSessionDeleted(sessionId, ok) {
  if (!ok) { showToast(`Failed to delete session: ${sessionId}`, "err"); return; }
  const el = document.querySelector(`#sessions-list [data-session-id="${CSS.escape(sessionId)}"]`);
  if (el) el.remove();
  if (getCurrentSessionId() === sessionId) {
    clearCurrentSessionId();
    resetChatSessionUiState();
  }
}

// ── Workspace tab strip ────────────────────────────────────────────────────

function _switchWorkspace(name) {
  const prev = state.activeWorkspace;
  state.activeWorkspace = name || null;
  try {
    if (state.activeWorkspace) {
      localStorage.setItem("hushclaw.ui.workspace", state.activeWorkspace);
    } else {
      localStorage.removeItem("hushclaw.ui.workspace");
    }
  } catch {}
  document.querySelectorAll("#workspace-tab-strip .ws-tab").forEach(btn => {
    btn.classList.toggle("active", (btn.dataset.ws || null) === state.activeWorkspace);
  });
  if (prev !== state.activeWorkspace) {
    clearCurrentSessionId();
    resetChatSessionUiState();
    refreshSessionsView();
    sendListMemories("", 50, false, 0);
  }
}

export function renderWorkspaceSelector(workspacesList) {
  state.workspacesList = workspacesList || [];
  const strip = document.getElementById("workspace-tab-strip");
  if (!strip) return;

  if (!state.workspacesList.length) {
    strip.classList.add("hidden");
    return;
  }

  const validNames = state.workspacesList.map(ws => ws.name);
  if (state.activeWorkspace && !validNames.includes(state.activeWorkspace)) {
    const prev = state.activeWorkspace;
    state.activeWorkspace = null;
    try { localStorage.removeItem("hushclaw.ui.workspace"); } catch {}
    if (prev) refreshSessionsView();
  }

  strip.innerHTML = "";
  const tabs = [
    { name: "", label: "Default", title: "Default workspace" },
    ...state.workspacesList.map(ws => ({ name: ws.name, label: ws.name, title: ws.path })),
  ];

  for (const { name, label, title } of tabs) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ws-tab" + ((state.activeWorkspace === (name || null)) ? " active" : "");
    btn.dataset.ws = name;
    btn.title = title;
    btn.textContent = label;
    btn.addEventListener("click", () => _switchWorkspace(name || null));
    strip.appendChild(btn);
  }

  strip.classList.remove("hidden");
}

// ── Memories panel ────────────────────────────────────────────────────────

export function renderMemories(items, hasMore = false, append = false) {
  // Track current state for "Load more" and post-delete refresh
  if (!append) {
    _memOffset = 0;
    _memQuery = els.memorySearch?.value?.trim() || "";
    _memIncludeAuto = document.getElementById("mem-show-auto")?.checked ?? false;
    _memKinds = selectedMemoryKinds();
  } else {
    _memOffset += items.length;
  }

  // Remove existing "Load more" sentinel before appending/replacing
  els.memoriesList.querySelector(".mem-load-more")?.remove();

  if (!append) {
    els.memoriesList.innerHTML = "";
    if (els.memoriesCount) els.memoriesCount.textContent = "";
    renderProfileSnapshot();
  }

  if (!items.length && !append) {
    els.memoriesList.innerHTML = '<div class="empty-state">No memories found.</div>';
    return;
  }

  const fmtTs = (raw) => {
    const n = Number(raw || 0);
    if (!Number.isFinite(n) || n <= 0) return "";
    return new Date(n * 1000).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  };

  let list = els.memoriesList.querySelector(".mem-list");
  if (!list) {
    list = document.createElement("div");
    list.className = "mem-list";
    els.memoriesList.appendChild(list);
  }

  items.forEach((m) => {
    const noteId = String(m.note_id ?? m.id ?? "").trim();
    const title  = m.title || m.content || m.text || "";
    const body   = m.body ? m.body.slice(0, 160) + (m.body.length > 160 ? "…" : "") : "";
    const rawTags = (m.tags || []).filter(t => t && !t.startsWith("_"));
    const tagsHtml = rawTags.length
      ? rawTags.map(t => `<span class="mem-tag">${escHtml(t)}</span>`).join("")
      : "";
    const scoreHtml = m.score != null
      ? `<span class="mem-score">${m.score.toFixed(2)}</span>`
      : "";
    const dateStr = fmtTs(m.created_at || m.created || 0);
    const dateHtml = dateStr ? `<span class="mem-date">${escHtml(dateStr)}</span>` : "";
    const footerItems = [tagsHtml, scoreHtml, dateHtml].filter(Boolean).join("");

    const card = document.createElement("div");
    card.className = "mem-card";
    card.dataset.noteId = noteId;
    card.innerHTML = `
      <div class="mem-card-left" title="Click to view full memory">
        <div class="mem-card-title">${escHtml(title)}</div>
        ${body ? `<div class="mem-card-body">${escHtml(body)}</div>` : ""}
        ${footerItems ? `<div class="mem-card-footer">${footerItems}</div>` : ""}
      </div>
      <div class="mem-card-right">
        <button class="mem-delete-btn icon-btn" data-note-id="${escHtml(noteId)}" title="Delete memory">✕</button>
      </div>
    `;

    card.querySelector(".mem-card-left").addEventListener("click", () => {
      const fullBody = m.body || m.content || "";
      const allTags  = (m.tags || []).filter(Boolean);
      const dateStr2 = fmtTs(m.created_at || m.created || 0);
      const tagsHtml2 = allTags.length
        ? `<div class="mem-modal-tags">${allTags.map(t => `<span class="mem-tag">${escHtml(t)}</span>`).join("")}</div>`
        : "";
      const metaHtml = [
        dateStr2  ? `<span class="mem-date">${escHtml(dateStr2)}</span>` : "",
        m.score != null ? `<span class="mem-score">${m.score.toFixed(3)}</span>` : "",
        `<span class="mem-id" title="note_id">${escHtml(noteId)}</span>`,
      ].filter(Boolean).join("");

      openDialog({
        title: title.slice(0, 100) || "Memory",
        html: `
          ${tagsHtml2 ? `<div class="mem-modal-meta">${tagsHtml2}<div class="mem-modal-info">${metaHtml}</div></div>` : (metaHtml ? `<div class="mem-modal-meta"><div class="mem-modal-info">${metaHtml}</div></div>` : "")}
          <pre class="mem-modal-body">${escHtml(fullBody || "—")}</pre>`,
        actions: [
          {
            label: "Delete",
            secondary: true,
            danger: true,
            onClick: async () => {
              const confirmed = await openConfirm({
                title: "Delete memory",
                message: `Delete "${title.slice(0, 60)}${title.length > 60 ? "…" : ""}"?`,
                confirmText: "Delete",
                cancelText: "Cancel",
                dangerConfirm: true,
              });
              if (confirmed) {
                send({ type: "delete_memory", note_id: noteId });
                closeModal();
              }
            },
          },
          { label: "Close", secondary: true, onClick: () => closeModal() },
        ],
      });
    });

    card.querySelector(".mem-delete-btn").addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const nid = ev.currentTarget.dataset.noteId;
      if (!nid) return;
      const confirmed = await openConfirm({
        title: "Delete memory",
        message: `Delete "${title.slice(0, 60)}${title.length > 60 ? "…" : ""}"?`,
        confirmText: "Delete",
        cancelText: "Cancel",
        dangerConfirm: true,
      });
      if (confirmed) send({ type: "delete_memory", note_id: nid });
    });
    list.appendChild(card);
  });

  // Update count badge with total visible items
  if (els.memoriesCount) {
    const visible = els.memoriesList.querySelectorAll(".mem-card").length;
    els.memoriesCount.textContent = visible ? String(visible) + (hasMore ? "+" : "") : "";
  }

  if (hasMore) {
    const btn = document.createElement("button");
    btn.className = "mem-load-more secondary";
    btn.textContent = "Load more…";
    btn.addEventListener("click", () => {
      btn.disabled = true;
      btn.textContent = "Loading…";
      const nextOffset = _memOffset;
      sendListMemories(_memQuery, 50, _memIncludeAuto, nextOffset, _memKinds);
    });
    els.memoriesList.appendChild(btn);
  }
}

export function renderProfileSnapshot() {
  if (!els.memoriesProfile) return;
  const text = String(learning.profileText || "").trim();
  if (!text) {
    els.memoriesProfile.classList.add("hidden");
    els.memoriesProfile.innerHTML = "";
    return;
  }
  const sections = text.split(/\n###\s+/).filter(Boolean);
  const html = sections.map((chunk, idx) => {
    const normalized = idx === 0 && chunk.startsWith("### ") ? chunk.slice(4) : chunk;
    const lines = normalized.split("\n").filter(Boolean);
    const title = lines.shift() || "Profile";
    const body = lines.map((line) => `<div class="mem-profile-line">${escHtml(line)}</div>`).join("");
    return `<div class="mem-profile-block"><div class="mem-profile-title">${escHtml(title)}</div>${body}</div>`;
  }).join("");
  els.memoriesProfile.classList.remove("hidden");
  els.memoriesProfile.innerHTML = `
    <div class="mem-profile-header">User Profile Snapshot</div>
    <div class="mem-profile-grid">${html}</div>
  `;
}

export function onMemoryDeleted(noteId, ok) {
  if (!ok) {
    showToast(`Failed to delete memory: ${noteId != null ? noteId : ""}`, "err");
    return;
  }
  // Re-fetch from offset 0 with current filter state
  sendListMemories(_memQuery, 50, _memIncludeAuto, 0, _memKinds);
}
