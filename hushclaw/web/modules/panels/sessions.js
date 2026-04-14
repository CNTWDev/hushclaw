/**
 * panels/sessions.js — Sessions sidebar, workspace selector, memories panel.
 */

import {
  state, els, send, sendListMemories, escHtml, showToast,
  getCurrentSessionId, setCurrentSessionId, clearCurrentSessionId,
} from "../state.js";
import { resetChatSessionUiState } from "../chat.js";
import { openConfirm, openDialog, closeModal } from "../modal.js";

// ── Memories pagination state ─────────────────────────────────────────────
let _memQuery = "";
let _memIncludeAuto = false;
let _memOffset = 0;

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
    const lastTs = s.last_turn
      ? new Date(s.last_turn * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
      : "";

    el.innerHTML = `
      <div class="sidebar-session-info">
        <div class="sidebar-session-title-row">
          <div class="sidebar-session-title" title="${escHtml(title)}">${escHtml(title)}</div>
          ${kindLabel ? `<span class="session-kind-badge">${kindLabel}</span>` : ""}
        </div>
        <div class="sidebar-session-meta">${s.turn_count || 0} turns${lastTs ? " · " + lastTs : ""} · ${escHtml(shortId)}</div>
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

// ── Workspace selector ─────────────────────────────────────────────────────

export function renderWorkspaceSelector(workspacesList) {
  state.workspacesList = workspacesList || [];
  const selector = document.getElementById("workspace-selector");
  const select   = document.getElementById("workspace-select");
  if (!selector || !select) return;

  if (!state.workspacesList.length) {
    selector.classList.add("hidden");
    return;
  }

  selector.classList.remove("hidden");
  select.innerHTML = '<option value="">(default)</option>' +
    state.workspacesList.map(ws =>
      `<option value="${escHtml(ws.name)}" title="${escHtml(ws.path)}">${escHtml(ws.name)}</option>`
    ).join("");

  const validNames = state.workspacesList.map(ws => ws.name);
  const desired = state.activeWorkspace;
  if (desired && validNames.includes(desired)) {
    select.value = desired;
  } else {
    select.value = "";
    const prevActive = state.activeWorkspace;
    state.activeWorkspace = null;
    try { localStorage.removeItem("hushclaw.ui.workspace"); } catch {}
    if (prevActive) {
      send({ type: "list_sessions", workspace: "" });
    }
  }
}

function _initWorkspaceSelectListener() {
  const select = document.getElementById("workspace-select");
  if (!select || select.dataset.wsListenerAttached) return;
  select.dataset.wsListenerAttached = "1";
  select.addEventListener("change", () => {
    const prev = state.activeWorkspace;
    state.activeWorkspace = select.value || null;
    try {
      if (state.activeWorkspace) {
        localStorage.setItem("hushclaw.ui.workspace", state.activeWorkspace);
      } else {
        localStorage.removeItem("hushclaw.ui.workspace");
      }
    } catch {}
    if (prev !== state.activeWorkspace) {
      clearCurrentSessionId();
      resetChatSessionUiState();
      send({ type: "list_sessions", workspace: state.activeWorkspace || "" });
      sendListMemories("", 50, false, 0);
    }
  });
}

// Attach listener once DOM is ready
setTimeout(_initWorkspaceSelectListener, 0);

// ── Memories panel ────────────────────────────────────────────────────────

export function renderMemories(items) {
export function renderMemories(items, hasMore = false, append = false) {
  // Track current state for "Load more" and post-delete refresh
  if (!append) {
    _memOffset = 0;
    _memQuery = els.memorySearch?.value?.trim() || "";
    _memIncludeAuto = document.getElementById("mem-show-auto")?.checked ?? false;
  } else {
    _memOffset += items.length;
  }

  // Remove existing "Load more" sentinel before appending/replacing
  els.memoriesList.querySelector(".mem-load-more")?.remove();

  if (!append) {
    els.memoriesList.innerHTML = "";
    if (els.memoriesCount) els.memoriesCount.textContent = "";
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
      sendListMemories(_memQuery, 50, _memIncludeAuto, nextOffset);
    });
    els.memoriesList.appendChild(btn);
  }
}

export function onMemoryDeleted(noteId, ok) {
  if (!ok) {
    showToast(`Failed to delete memory: ${noteId != null ? noteId : ""}`, "err");
    return;
  }
  // Re-fetch from offset 0 with current filter state
  sendListMemories(_memQuery, 50, _memIncludeAuto, 0);
}
