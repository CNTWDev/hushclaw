/**
 * panels/sessions.js — Sessions sidebar, workspace selector, memories panel.
 */

import {
  state, els, learning, send, sendListMemories, escHtml, showToast,
  getCurrentSessionId, setCurrentSessionId, clearCurrentSessionId,
} from "../state.js";
import { resetChatSessionUiState, saveScrollPosition } from "../chat.js";
import { openConfirm, openDialog, closeModal } from "../modal.js";
import { t } from "../i18n.js";

// ── Memories pagination state ─────────────────────────────────────────────
let _memQuery = "";
let _memIncludeAuto = false;
let _memOffset = 0;
let _memKinds = ["user_model", "project_knowledge", "decision"];

// ── Sessions pagination state ─────────────────────────────────────────────
let _sessionQuery = "";
let _sessionOffset = 0;
let _sessionLimit = 30;
let _sessionHasMore = false;

const SESSIONS_COLLAPSED_KEY = "hushclaw.ui.sessions-collapsed";
let _sessionsCollapsed = false;

const PROFILE_CATEGORY_LABELS = {
  communication_style: "How I Communicate",
  expertise: "Expertise",
  avoidances: "Avoidances",
  workflow_habits: "How I Work",
  tooling_preferences: "Tooling",
  domains_of_interest: "What I Care About",
  recurring_goals: "Recurring Goals",
  preferences: "Preferences",
};

function _fmtShortDate(epoch) {
  const n = Number(epoch || 0);
  if (!Number.isFinite(n) || n <= 0) return "";
  return new Date(n * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
}

function _displayProfileCategory(category) {
  return PROFILE_CATEGORY_LABELS[category] || String(category || "Profile").replaceAll("_", " ");
}

function _profileCategoryClass(category) {
  return String(category || "misc").toLowerCase().replace(/[^a-z0-9_-]/g, "");
}

function _clipText(value, max = 150) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > max ? text.slice(0, max - 1).trimEnd() + "…" : text;
}

function _sourceLabel(source) {
  if (!source) return "";
  if (source.type === "session") {
    return _clipText(source.title || source.session_id || "Session", 42);
  }
  if (source.type === "note") {
    return _clipText(source.title || source.note_id || "Memory note", 42);
  }
  return "";
}

function _renderSourceChip(source, { compact = false } = {}) {
  const label = _sourceLabel(source);
  if (!label) return "";
  if (source.type === "session" && source.session_id) {
    return `
      <button class="mem-source-chip session-source${compact ? " compact" : ""}"
              data-source-session="${escHtml(source.session_id)}"
              title="Open source session: ${escHtml(source.title || source.session_id)}">
        Source: ${escHtml(label)}
      </button>
    `;
  }
  return `
    <span class="mem-source-chip${compact ? " compact" : ""}"
          title="${escHtml(source.note_id || source.title || label)}">
      Evidence: ${escHtml(label)}
    </span>
  `;
}

function _wireMemorySourceLinks(root = document) {
  root.querySelectorAll(".mem-source-chip.session-source[data-source-session]").forEach((btn) => {
    if (btn.dataset.wired === "1") return;
    btn.dataset.wired = "1";
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const sid = btn.dataset.sourceSession;
      if (!sid) return;
      import("./agents.js").then(({ switchTab }) => switchTab("chat"));
      loadSession(sid);
    });
  });
}

function _wireProfileDeleteButtons(root = document) {
  root.querySelectorAll(".mem-profile-delete[data-profile-fact-id]").forEach((btn) => {
    if (btn.dataset.wired === "1") return;
    btn.dataset.wired = "1";
    btn.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const factId = btn.dataset.profileFactId || "";
      if (!factId) return;
      const label = btn.dataset.profileLabel || "this profile fact";
      const confirmed = await openConfirm({
        title: "Remove profile fact",
        message: `Remove "${_clipText(label, 80)}" from the long-term profile? Source memories are kept.`,
        confirmText: "Remove",
        cancelText: "Cancel",
        dangerConfirm: true,
      });
      if (confirmed) send({ type: "delete_profile_fact", fact_id: factId });
    });
  });
}

function _profileFactText(fact) {
  const vj = fact?.value_json;
  if (typeof vj === "object" && vj !== null) {
    return String(vj.summary ?? vj.value ?? JSON.stringify(vj));
  }
  return String(fact?.value ?? fact?.key ?? vj ?? "");
}

function _confidencePct(value) {
  return Math.round(Math.min(1, Math.max(0, Number(value || 0))) * 100);
}

const TIME_HORIZON_LABELS = {
  now: "Now",
  recent: "Recent",
  mid_term: "Mid-term",
  long_term: "Long-term",
  learning: "Learning",
};

const STABILITY_LABELS = {
  volatile: "volatile",
  decaying: "decays",
  consolidating: "forming",
  stable: "stable",
};

function _horizonLabel(value) {
  return TIME_HORIZON_LABELS[value] || String(value || "memory").replaceAll("_", " ");
}

function _stabilityLabel(value) {
  return STABILITY_LABELS[value] || String(value || "unknown").replaceAll("_", " ");
}

function _weightPct(item) {
  return Math.round(Math.min(1, Math.max(0, Number(item?.effective_weight ?? 0))) * 100);
}

function _ageLabel(item) {
  const age = Number(item?.age_days ?? 0);
  if (!Number.isFinite(age) || age <= 0) return "today";
  if (age < 1) return "<1d";
  return `${Math.round(age)}d`;
}

function _renderLayerCard({ id, title, subtitle, item, metric = "", body = "" }) {
  const horizon = item?.time_horizon || id;
  const stability = item?.stability || "";
  return `
    <section class="mem-time-card horizon-${escHtml(horizon)}">
      <div class="mem-time-card-top">
        <span>${escHtml(title)}</span>
        ${metric ? `<b>${escHtml(metric)}</b>` : ""}
      </div>
      <div class="mem-time-card-title">${escHtml(subtitle)}</div>
      <div class="mem-time-meta">
        <span>${escHtml(_horizonLabel(horizon))}</span>
        ${stability ? `<span>${escHtml(_stabilityLabel(stability))}</span>` : ""}
        <span>${_weightPct(item)}%</span>
        <span>${escHtml(_ageLabel(item))}</span>
      </div>
      ${body ? `<div class="mem-time-card-body">${body}</div>` : ""}
    </section>
  `;
}

function _profileCloudItems(items, limit = 18) {
  return (Array.isArray(items) ? items : [])
    .map((f) => ({
      fact_id: f.fact_id || "",
      category: f.category || "preferences",
      key: f.key || "",
      text: _profileFactText(f),
      confidence: Math.min(1, Math.max(0, Number(f.confidence || 0))),
      updated: Number(f.updated || 0),
    }))
    .filter((f) => f.text || f.key)
    .sort((a, b) => (b.confidence - a.confidence) || (b.updated - a.updated))
    .slice(0, limit);
}

function _renderProfileCloud(items, { compact = false, animated = false } = {}) {
  const facts = _profileCloudItems(items, compact ? 12 : 28);
  if (!facts.length) {
    return `<div class="mem-ov-empty">No profile signals yet.</div>`;
  }
  const renderWord = (f) => {
    const pct = _confidencePct(f.confidence);
    const size = compact
      ? 11 + f.confidence * 7
      : 11.5 + f.confidence * 9.5;
    const opacity = 0.58 + f.confidence * 0.38;
    const label = _clipText(f.text || f.key, compact ? 34 : 54);
    const source = _sourceLabel(f.source);
    const title = `${_displayProfileCategory(f.category)} · ${pct}%${source ? " · " + source : ""} · ${f.text || f.key}`;
    return `
      <span class="mem-persona-word cat-${escHtml(_profileCategoryClass(f.category))}"
            style="font-size:${size.toFixed(1)}px;opacity:${opacity.toFixed(2)}"
            title="${escHtml(title)}"
            data-profile-fact-id="${escHtml(f.fact_id)}"
            data-profile-label="${escHtml(f.text || f.key)}">
        ${escHtml(label)}
        ${f.fact_id ? `<button class="mem-profile-delete" title="Remove profile fact" data-profile-fact-id="${escHtml(f.fact_id)}" data-profile-label="${escHtml(f.text || f.key)}">×</button>` : ""}
      </span>
    `;
  };
  if (!animated) return facts.map(renderWord).join("");
  const lanes = [[], [], []];
  facts.forEach((f, idx) => lanes[idx % lanes.length].push(f));
  return lanes
    .filter(lane => lane.length)
    .map((lane, idx) => `
      <div class="mem-profile-lane lane-${idx}">
        <div class="mem-profile-lane-track">
          ${lane.map(renderWord).join("")}
          ${lane.map(renderWord).join("")}
        </div>
      </div>
    `).join("");
}

function _beliefStrength(model) {
  const entries = Array.isArray(model?.entries) ? model.entries.length : 0;
  const recency = Number(model?.updated || 0) > 0 ? 1 : 0;
  return Math.min(1, 0.25 + entries * 0.09 + recency * 0.18 + (model?.dirty ? 0.08 : 0));
}

function _renderBeliefConstellation(items) {
  const beliefs = (Array.isArray(items) ? items : []).slice(0, 7);
  if (!beliefs.length) {
    return `<div class="mem-ov-empty">No belief model has formed yet.</div>`;
  }
  return beliefs.map((b) => {
    const strength = _beliefStrength(b);
    const entries = Array.isArray(b.entries) ? b.entries.length : 0;
    const signals = (b.signals || []).slice(0, 2).join(" · ");
    const source = (b.entries || []).find(e => e.source)?.source;
    const size = 74 + strength * 34;
    return `
      <div class="mem-belief-node${b.dirty ? " dirty" : ""}"
           style="width:${size.toFixed(0)}px;height:${size.toFixed(0)}px"
           title="${escHtml(_clipText([b.summary || b.latest || b.domain, _sourceLabel(source)].filter(Boolean).join(" · "), 220))}">
        <strong>${escHtml(_clipText(b.domain || "general", 22))}</strong>
        <span>${entries} signal${entries === 1 ? "" : "s"}</span>
        ${signals ? `<small>${escHtml(_clipText(signals, 42))}</small>` : ""}
      </div>
    `;
  }).join("");
}

function _renderLearningTimeline(reflections) {
  const refs = (Array.isArray(reflections) ? reflections : []).slice(0, 5);
  if (!refs.length) {
    return `<div class="mem-ov-empty">No task reflections yet.</div>`;
  }
  return refs.map((r) => {
    const ok = !!r.success;
    const dateStr = _fmtShortDate(r.created);
    return `
      <div class="mem-persona-timeline-item ${ok ? "ok" : "fail"}">
        <span class="mem-persona-timeline-dot"></span>
        <div>
          <strong>${escHtml(_clipText(r.lesson || r.outcome || "", 112))}</strong>
          ${r.failure_mode ? `<small>${escHtml(_clipText(r.failure_mode, 90))}</small>` : ""}
          ${_renderSourceChip(r.source, { compact: true })}
          ${dateStr ? `<time>${escHtml(dateStr)}</time>` : ""}
        </div>
      </div>
    `;
  }).join("");
}

function _renderIdentityMetric(label, value, detail = "") {
  return `
    <div class="mem-identity-metric">
      <strong>${escHtml(value)}</strong>
      <span>${escHtml(label)}</span>
      ${detail ? `<small>${escHtml(detail)}</small>` : ""}
    </div>
  `;
}

function _renderMemoryStrata(items) {
  const list = (Array.isArray(items) ? items : []).slice(0, 6);
  if (!list.length) {
    return `<div class="mem-ov-empty">No decaying recent memories yet.</div>`;
  }
  return list.map((m, idx) => {
    const weight = _weightPct(m);
    const opacity = Math.max(0.38, 1 - idx * 0.1);
    return `
      <div class="mem-strata-row" style="opacity:${opacity.toFixed(2)}">
        <span>${escHtml(_ageLabel(m))}</span>
        <strong>${escHtml(_clipText(m.title || m.body, 96))}</strong>
        <div class="mem-strata-weight"><i style="width:${weight}%"></i></div>
      </div>
    `;
  }).join("");
}

function _renderLearningPulse(reflections) {
  const refs = (Array.isArray(reflections) ? reflections : []).slice(0, 8);
  if (!refs.length) {
    return `<div class="mem-learning-wave empty"></div>`;
  }
  return `
    <div class="mem-learning-wave">
      ${refs.map((r, idx) => {
        const height = r.success ? 22 + (idx % 3) * 8 : 42;
        return `<span class="${r.success ? "ok" : "fail"}" style="height:${height}px"></span>`;
      }).join("")}
    </div>
  `;
}

function _renderBeliefConstellationPanel(items) {
  return `
    <div class="mem-belief-constellation identity">${_renderBeliefConstellation(items)}</div>
  `;
}

// ── Sessions sidebar ──────────────────────────────────────────────────────

export function loadSession(session_id) {
  saveScrollPosition(getCurrentSessionId());
  setCurrentSessionId(session_id);
  document.querySelectorAll(".sidebar-session").forEach((el) => {
    el.classList.toggle("active", el.dataset.sessionId === session_id);
  });
  send({ type: "get_session_history", session_id });
}

export function renderSessions(items, hasMore = false, append = false) {
  const list = document.getElementById("sessions-list");
  if (!list) return;

  _sessionHasMore = hasMore;

  // Remove existing "Load more" sentinel before appending/replacing
  list.querySelector(".load-more-row")?.remove();

  if (!append) {
    _sessionOffset = 0;
    list.innerHTML = "";
  }

  if (!items.length && !append) {
    list.innerHTML = `<div class="empty-state" style="padding:12px;font-size:11px">${t("no_sessions")}</div>`;
    state._firstSessionLoad = false;
    return;
  }

  _sessionOffset += items.length;

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
        <div class="sidebar-session-meta">${s.turn_count || 0} ${t("turns")}${lastTs ? " · " + lastTs : ""}${metaExtras ? " · " + escHtml(metaExtras) : ""} · ${escHtml(shortId)}</div>
        ${lastPreview ? `<div class="sidebar-session-preview">${escHtml(lastPreview)}</div>` : ""}
      </div>
      <div class="session-item-actions">
        <button class="session-move-btn" data-session-id="${escHtml(s.session_id || "")}" title="Move to workspace">⇄</button>
        <button class="session-delete-btn" data-session-id="${escHtml(s.session_id || "")}" title="Delete session">✕</button>
      </div>
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
    el.querySelector(".session-move-btn").addEventListener("click", (ev) => {
      ev.stopPropagation();
      const sid = ev.currentTarget.dataset.sessionId;
      if (!sid) return;
      _showMoveWorkspacePopover(ev.currentTarget, sid, s.workspace || "");
    });
    el.addEventListener("click", () => loadSession(s.session_id));
    list.appendChild(el);
  });

  if (hasMore) {
    const wrap = document.createElement("div");
    wrap.className = "load-more-row";
    const btn = document.createElement("button");
    btn.className = "secondary load-more-btn";
    btn.textContent = "Load more…";
    btn.addEventListener("click", () => {
      btn.disabled = true;
      btn.textContent = "Loading…";
      send({
        type: "list_sessions",
        workspace: state.activeWorkspace || "",
        offset: _sessionOffset,
        limit: _sessionLimit,
      });
    });
    wrap.appendChild(btn);
    list.appendChild(wrap);
  }

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
  _sessionOffset = 0;
  if (_sessionQuery) {
    send({
      type: "search_sessions",
      query: _sessionQuery,
      workspace: state.activeWorkspace || "",
    });
    return;
  }
  send({
    type: "list_sessions",
    workspace: state.activeWorkspace || "",
    offset: 0,
    limit: _sessionLimit,
  });
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
  _sessionOffset = 0;
  if (els.sessionSearch) els.sessionSearch.value = "";
  send({
    type: "list_sessions",
    workspace: state.activeWorkspace || "",
    offset: 0,
    limit: _sessionLimit,
  });
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
  let saved = null;
  try { saved = localStorage.getItem(SESSIONS_COLLAPSED_KEY); } catch {}
  const defaultCollapsed = window.innerWidth <= 960;
  _applySessionsCollapsed(saved !== null ? saved === "1" : defaultCollapsed);
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

export function handleSessionWorkspaceMoved(data) {
  if (!data.ok) { showToast(data.error || "Failed to move session", "error"); return; }
  showToast(`Session moved to workspace: ${data.workspace || "Default"}`, "info");
  refreshSessionsView();
}

let _movePopover = null;

function _showMoveWorkspacePopover(anchorEl, sessionId, currentWorkspace) {
  if (_movePopover) { _movePopover.remove(); _movePopover = null; }

  const workspaces = [
    { name: "", label: "Default" },
    ...(state.workspacesList || []).map(ws => ({ name: ws.name, label: ws.name })),
  ];

  const pop = document.createElement("div");
  pop.className = "session-move-popover";
  pop.innerHTML = `<div class="session-move-popover-title">Move to workspace</div>` +
    workspaces.map(ws => `
      <button class="session-move-popover-item${ws.name === currentWorkspace ? " active" : ""}" data-ws="${escHtml(ws.name)}">
        ${escHtml(ws.label)}${ws.name === currentWorkspace ? " ✓" : ""}
      </button>`).join("");

  const rect = anchorEl.getBoundingClientRect();
  pop.style.position = "fixed";
  pop.style.top = `${rect.bottom + 4}px`;
  pop.style.left = `${rect.left}px`;
  pop.style.zIndex = "9999";

  pop.querySelectorAll(".session-move-popover-item").forEach(btn => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const ws = btn.dataset.ws;
      send({ type: "move_session_workspace", session_id: sessionId, workspace: ws });
      pop.remove();
      _movePopover = null;
    });
  });

  document.body.appendChild(pop);
  _movePopover = pop;

  const dismiss = (ev) => {
    if (!pop.contains(ev.target) && ev.target !== anchorEl) {
      pop.remove();
      _movePopover = null;
      document.removeEventListener("click", dismiss, true);
    }
  };
  setTimeout(() => document.addEventListener("click", dismiss, true), 0);
}

// ── Workspace tab strip ────────────────────────────────────────────────────

function _workspaceTone(name) {
  if (!name) return "default";
  const tones = ["emerald", "sky", "violet", "rose", "amber", "indigo"];
  let hash = 0;
  for (const ch of String(name)) hash = ((hash * 31) + ch.charCodeAt(0)) >>> 0;
  return tones[hash % tones.length];
}

function _switchWorkspace(name) {
  toggleSessionsSidebar(false);
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
    const isActive = (btn.dataset.ws || null) === state.activeWorkspace;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-pressed", isActive ? "true" : "false");
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
    btn.dataset.tone = _workspaceTone(name);
    btn.title = title;
    btn.textContent = label;
    btn.setAttribute("aria-label", `${label} workspace`);
    btn.setAttribute("aria-pressed", (state.activeWorkspace === (name || null)) ? "true" : "false");
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
  els.memoriesList.querySelector(".load-more-row")?.remove();

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

  if (!append) {
    const hdr = document.createElement("div");
    hdr.className = "mem-list-header";
    hdr.innerHTML = `<span>Kind</span><span>Memory</span><span class="mem-col-r">Score</span><span class="mem-col-r">Date</span><span></span>`;
    list.appendChild(hdr);
  }

  const KIND_LABEL = {
    user_model: "user", project_knowledge: "proj",
    decision: "dec", session_memory: "sess", telemetry: "tel",
  };

  items.forEach((m) => {
    const noteId  = String(m.note_id ?? m.id ?? "").trim();
    const title   = m.title || m.content || m.text || "";
    const dateStr = fmtTs(m.created_at || m.created || 0);
    const kindLabel = KIND_LABEL[m.kind] || (m.kind || "").slice(0, 4) || "—";

    const card = document.createElement("div");
    card.className = "mem-card";
    card.dataset.noteId = noteId;
    card.innerHTML = `
      <span class="mem-kind-badge" title="${escHtml(m.kind || "")}">${escHtml(kindLabel)}</span>
      <span class="mem-card-title">${escHtml(title)}</span>
      <span class="mem-score">${m.score != null ? m.score.toFixed(2) : ""}</span>
      <span class="mem-date">${escHtml(dateStr)}</span>
      <button class="mem-delete-btn icon-btn" data-note-id="${escHtml(noteId)}" title="Delete memory">✕</button>
    `;

    card.addEventListener("click", (ev) => {
      if (ev.target.closest(".mem-delete-btn")) return;
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
  _updateOvCount("ov-notes-count", els.memoriesList.querySelectorAll(".mem-card").length);
  _wireSubtabs();

  if (hasMore) {
    const wrap = document.createElement("div");
    wrap.className = "load-more-row";
    const btn = document.createElement("button");
    btn.className = "secondary load-more-btn";
    btn.textContent = "Load more…";
    btn.addEventListener("click", () => {
      btn.disabled = true;
      btn.textContent = "Loading…";
      const nextOffset = _memOffset;
      sendListMemories(_memQuery, 50, _memIncludeAuto, nextOffset, _memKinds);
    });
    wrap.appendChild(btn);
    els.memoriesList.appendChild(wrap);
  }
}

export function renderMemoryOverview(data) {
  if (!els.memoriesOverview) return;
  const profile = data?.profile || {};
  const beliefs = data?.beliefs || {};
  const reflections = data?.reflections || {};
  const memories = data?.memories || {};
  const taxonomy = data?.taxonomy || {};

  const profileFacts = profile.high_confidence_facts || [];
  const beliefDomains = beliefs.top_domains || [];
  const lessons = reflections.latest_lessons || [];
  const recent = memories.recent_items || [];
  const decayingRecent = recent.filter(m => (m.time_horizon || "recent") === "recent");
  const successCount = Number(reflections.success_count || 0);
  const failureCount = Number(reflections.failure_count || 0);
  const totalRuns = successCount + failureCount;
  const successPct = totalRuns ? Math.round((successCount / totalRuns) * 100) : 0;

  const topProfile = profileFacts[0] || {};
  const topBelief = beliefDomains[0] || {};
  const topRecent = decayingRecent[0] || {};
  const topLesson = lessons[0] || {};
  const contextInfo = taxonomy.context || {
    time_horizon: "now",
    stability: "volatile",
    effective_weight: 1,
    age_days: 0,
  };

  const timeLayerHtml = `
    ${_renderLayerCard({
      id: "now",
      title: "Now / Context",
      subtitle: "当前会话优先",
      item: contextInfo,
      metric: "highest",
      body: "这里展示持久化 working state 和运行时规则；当前消息与引用只在回答时组装，不存入 memory overview。"
    })}
    ${_renderLayerCard({
      id: "recent",
      title: "Recent Memory",
      subtitle: "近期证据池",
      item: topRecent,
      metric: `${decayingRecent.length}`,
      body: decayingRecent.length ? escHtml(_clipText(topRecent.title || topRecent.body, 72)) : "只显示会随时间衰减的普通记忆；画像和信念会进入更稳定的层。"
    })}
    ${_renderLayerCard({
      id: "mid_term",
      title: "Mid-term Models",
      subtitle: "信念 / 理念",
      item: topBelief,
      metric: `${Number(beliefs.total || 0)}`,
      body: topBelief.domain ? escHtml(_clipText(topBelief.domain, 72)) : "由多条证据合成，比记忆稳定，比画像更领域化。"
    })}
    ${_renderLayerCard({
      id: "long_term",
      title: "Long-term Profile",
      subtitle: "稳定用户画像",
      item: topProfile,
      metric: `${Number(profile.total || 0)}`,
      body: topProfile.value ? escHtml(_clipText(topProfile.value, 72)) : "慢更新，高置信，不应被单次对话轻易覆盖。"
    })}
    ${_renderLayerCard({
      id: "learning",
      title: "Learning Loop",
      subtitle: "执行复盘",
      item: topLesson,
      metric: `${Number(reflections.total_recent || 0)}`,
      body: lessons.length ? escHtml(_clipText(topLesson.lesson || topLesson.outcome, 72)) : "agent 自己的经验沉淀，按任务类型复用。"
    })}
  `;

  els.memoriesOverview.innerHTML = `
    <section class="mem-identity-hero">
      <div class="mem-identity-copy">
        <div class="mem-ov-eyebrow">Identity Core</div>
        <h2><span>用户画像、信念与记忆</span><span>正在形成可追溯的心智星图</span></h2>
        <p>Context stays first. Memories decay. Profile stabilizes. Beliefs evolve.</p>
        <div class="mem-time-priority">
          <span>Concept: Now first</span><span>Actual: Profile</span><span>Beliefs</span><span>Working state</span><span>Recall</span>
        </div>
      </div>
      <div class="mem-identity-core" aria-label="User identity core">
        <div class="mem-core-ring ring-outer"><span>Recent Memory</span></div>
        <div class="mem-core-ring ring-middle"><span>Beliefs</span></div>
        <div class="mem-core-ring ring-inner"><span>Profile</span></div>
        <div class="mem-core-orb">
          <strong>${Number(profile.total || 0)}</strong>
          <span>Portrait facts</span>
        </div>
        <i class="core-node node-a"></i>
        <i class="core-node node-b"></i>
        <i class="core-node node-c"></i>
        <i class="core-node node-d"></i>
      </div>
      <div class="mem-identity-metrics">
        ${_renderIdentityMetric("Profile", Number(profile.total || 0), "long-term")}
        ${_renderIdentityMetric("Beliefs", Number(beliefs.total || 0), `${Number(beliefs.dirty_count || 0)} pending`)}
        ${_renderIdentityMetric("Decaying", decayingRecent.length, "recent")}
        ${_renderIdentityMetric("Clean", `${successPct}%`, "learning")}
      </div>
    </section>

    <div class="mem-time-grid identity">${timeLayerHtml}</div>

    <div class="mem-identity-grid">
      <section class="mem-persona-panel mem-nebula-panel">
        <div class="mem-ov-card-hdr"><span>Portrait Nebula</span><b>${Number(profile.total || 0)}</b></div>
        <div class="mem-profile-cloud compact animated">${_renderProfileCloud(profileFacts, { compact: true, animated: true })}</div>
      </section>
      <section class="mem-persona-panel mem-persona-panel-beliefs">
        <div class="mem-ov-card-hdr"><span>Belief Map</span><b>${Number(beliefs.dirty_count || 0)} pending</b></div>
        ${_renderBeliefConstellationPanel(beliefDomains)}
      </section>
      <section class="mem-persona-panel mem-persona-panel-reflect">
        <div class="mem-ov-card-hdr"><span>Learning Loop</span><b>${Number(reflections.total_recent || 0)}</b></div>
        ${_renderLearningPulse(lessons)}
        <div class="mem-persona-timeline">${_renderLearningTimeline(lessons)}</div>
      </section>
      <section class="mem-persona-panel mem-strata-panel">
        <div class="mem-ov-card-hdr"><span>Memory Strata</span><b>${decayingRecent.length}</b></div>
        <div class="mem-strata-list">${_renderMemoryStrata(decayingRecent)}</div>
      </section>
    </div>
  `;
  _wireSubtabs();
  _wireMemorySourceLinks(els.memoriesOverview);
  _wireProfileDeleteButtons(els.memoriesOverview);
}

export function renderProfileSnapshot() {
  if (!els.memoriesProfile) return;
  const text = String(learning.profileText || "").trim();
  if (!text) {
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
  els.memoriesProfile.innerHTML = `
    <div class="mem-profile-header">User Profile Snapshot</div>
    <div class="mem-profile-grid">${html}</div>
  `;
}

const _BELIEF_PAGE = 25;

export function renderBeliefModels(items) {
  if (!els.memoriesBeliefs) return;
  if (!items || !items.length) {
    els.memoriesBeliefs.innerHTML = `
      <div class="mem-beliefs-hdr"><span class="mem-beliefs-label">Domain Beliefs</span></div>
      <div class="mem-section-empty">No domain beliefs yet.<br>Deep conversations in a topic area will be distilled automatically.</div>
    `;
    _updateOvCount("ov-beliefs-count", 0);
    return;
  }

  const fmtTs = (epoch) => {
    const n = Number(epoch || 0);
    if (!n) return "";
    return new Date(n * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
  };

  const renderCardItems = (list) => list.map((m) => {
    const signalsHtml = (m.signals || []).map(s =>
      `<span class="mem-belief-tag">${escHtml(s)}</span>`
    ).join("");
    const count = (m.entries || []).length;
    const dateStr = fmtTs(m.updated);
    const dirtyDot = m.dirty ? `<span class="mem-belief-dirty" title="Pending merge">●</span>` : "";

    const entriesHtml = (m.entries || []).map(e => {
      const typeClass = (e.note_type || "belief").replace(/[^a-z]/g, "");
      const eDate = fmtTs(e.timestamp);
      return `<div class="mem-belief-entry">
        <span class="mem-belief-entry-type ${escHtml(typeClass)}">${escHtml(e.note_type || "belief")}</span>
        <span class="mem-belief-entry-content">${escHtml(e.content || "")}</span>
        ${_renderSourceChip(e.source, { compact: true })}
        ${eDate ? `<span class="mem-belief-entry-date">${escHtml(eDate)}</span>` : ""}
      </div>`;
    }).join("");

    return `
      <div class="mem-belief-card">
        <div class="mem-belief-hdr mem-belief-toggle">
          <span class="mem-belief-domain">${escHtml(m.domain)}</span>
          ${count ? `<span class="mem-belief-count">${count}</span>` : ""}
          ${dirtyDot}
          ${dateStr ? `<span class="mem-belief-date">${escHtml(dateStr)}</span>` : ""}
          <span class="mem-belief-chevron">›</span>
        </div>
        ${m.summary ? `<div class="mem-belief-summary">${escHtml(m.summary)}</div>` : ""}
        ${m.trajectory ? `<div class="mem-belief-trajectory">${escHtml(m.trajectory)}</div>` : ""}
        ${signalsHtml ? `<div class="mem-belief-signals">${signalsHtml}</div>` : ""}
        ${entriesHtml ? `<div class="mem-belief-entries" style="display:none">${entriesHtml}</div>` : ""}
      </div>
    `;
  }).join("");

  let _beliefOffset = Math.min(_BELIEF_PAGE, items.length);
  const hasMore = items.length > _BELIEF_PAGE;

  els.memoriesBeliefs.innerHTML = `
    <div class="mem-beliefs-hdr">
      <span class="mem-beliefs-label">Domain Beliefs</span>
      <span class="mem-beliefs-count">${items.length}</span>
    </div>
    <div class="mem-beliefs-list" id="mem-beliefs-list-body">${renderCardItems(items.slice(0, _beliefOffset))}</div>
    ${hasMore ? `<div class="load-more-row"><button class="secondary load-more-btn" id="mem-beliefs-load-more">More (${items.length - _beliefOffset})</button></div>` : ""}
  `;

  // Delegated toggle: covers both initial cards and cards added by "更多"
  document.getElementById("mem-beliefs-list-body")?.addEventListener("click", (e) => {
    const hdr = e.target.closest(".mem-belief-toggle");
    if (!hdr) return;
    const card = hdr.closest(".mem-belief-card");
    const entriesEl = card?.querySelector(".mem-belief-entries");
    if (!entriesEl) return;
    const open = entriesEl.style.display !== "none";
    entriesEl.style.display = open ? "none" : "";
    hdr.querySelector(".mem-belief-chevron")?.classList.toggle("open", !open);
  });

  if (hasMore) {
    document.getElementById("mem-beliefs-load-more")?.addEventListener("click", function () {
      const listEl = document.getElementById("mem-beliefs-list-body");
      if (!listEl) return;
      const next = items.slice(_beliefOffset, _beliefOffset + _BELIEF_PAGE);
      listEl.insertAdjacentHTML("beforeend", renderCardItems(next));
      _wireMemorySourceLinks(listEl);
      _beliefOffset += next.length;
      const remaining = items.length - _beliefOffset;
      if (remaining <= 0) {
        this.closest(".load-more-row")?.remove();
      } else {
        this.textContent = `More (${remaining})`;
      }
    });
  }
  _updateOvCount("ov-beliefs-count", items.length);
  _wireMemorySourceLinks(els.memoriesBeliefs);
}

export function renderProfileFacts(items) {
  if (!els.memoriesProfile) return;
  if (!items || !items.length) {
    els.memoriesProfile.innerHTML = `
      <div class="mem-profile-header">User Profile</div>
      <div class="mem-section-empty">No profile data yet.<br>Profile preferences and habits are automatically extracted after tasks with tool calls.</div>
    `;
    _updateOvCount("ov-profile-count", 0);
    return;
  }

  const CATEGORY_LABELS = {
    communication_style:  "Communication Style",
    expertise:            "Expertise",
    avoidances:           "Avoidances",
    workflow_habits:      "Workflow Habits",
    tooling_preferences:  "Tooling Preferences",
    domains_of_interest:  "Domains of Interest",
    recurring_goals:      "Recurring Goals",
    preferences:          "Preferences",
  };

  const fmtTs = (epoch) => {
    const n = Number(epoch || 0);
    if (!n) return "";
    return new Date(n * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
  };

  // Group by category, preserving order of first occurrence
  const order = [];
  const groups = {};
  for (const f of items) {
    const cat = f.category || "misc";
    if (!groups[cat]) { groups[cat] = []; order.push(cat); }
    groups[cat].push(f);
  }

  const sectionsHtml = order.map((cat) => {
    const facts = groups[cat];
    const label = CATEGORY_LABELS[cat] || cat;
    const factsHtml = facts.map(f => {
      const val = _profileFactText(f);
      const conf = _confidencePct(f.confidence);
      const dateStr = fmtTs(f.updated);
      return `
        <div class="mem-pf-item">
          <div class="mem-pf-key-row">
            <div class="mem-pf-key">${escHtml(f.key || "")}</div>
            ${f.fact_id ? `<button class="mem-profile-delete mem-pf-delete" title="Remove profile fact" data-profile-fact-id="${escHtml(f.fact_id)}" data-profile-label="${escHtml(String(val))}">×</button>` : ""}
          </div>
          <div class="mem-pf-value">${escHtml(String(val))}</div>
          <div class="mem-pf-meta">
            <div class="mem-pf-conf"><div class="mem-pf-conf-bar" style="width:${conf}%"></div></div>
            <span class="mem-pf-conf-num">${conf}%</span>
            ${_renderSourceChip(f.source, { compact: true })}
            ${dateStr ? `<span class="mem-pf-date">${escHtml(dateStr)}</span>` : ""}
          </div>
        </div>
      `;
    }).join("");

    return `
      <div class="mem-pf-section">
        <div class="mem-pf-cat-title">${escHtml(label)}<span class="mem-pf-cat-count">${facts.length}</span></div>
        ${factsHtml}
      </div>
    `;
  }).join("");

  els.memoriesProfile.innerHTML = `
    <div class="mem-profile-header">User Profile <span class="mem-pf-total">${items.length} items</span></div>
    <section class="mem-profile-cloud-panel">
      <div class="mem-profile-cloud-title">
        <span>Portrait Cloud</span>
        <b>size = confidence</b>
      </div>
      <div class="mem-profile-cloud">${_renderProfileCloud(items)}</div>
    </section>
    <div class="mem-pf-content">${sectionsHtml}</div>
  `;
  _updateOvCount("ov-profile-count", items.length);
  _wireMemorySourceLinks(els.memoriesProfile);
  _wireProfileDeleteButtons(els.memoriesProfile);
}

export function onProfileFactDeleted(factId, ok) {
  if (!ok) {
    showToast(`Failed to remove profile fact: ${factId || ""}`, "err");
    return;
  }
  showToast("Profile fact removed. Source memories were kept.", "ok");
  send({ type: "get_memory_overview" });
  send({ type: "list_profile_facts" });
}

// ── Overview strip helpers ────────────────────────────────────────────────

function _updateOvCount(id, n) {
  const el = document.getElementById(id);
  if (el) el.textContent = String(n);
}

// Wire up sub-tab switching (called lazily on first renderMemories)
let _subtabsWired = false;
function _wireSubtabs() {
  if (_subtabsWired) return;
  _subtabsWired = true;
  document.querySelectorAll(".mem-subtab[data-sub]").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mem-subtab").forEach(b =>
        b.classList.toggle("active", b === btn));
      document.querySelectorAll(".mem-sub").forEach(s =>
        s.classList.toggle("active", s.id === `mem-sub-${btn.dataset.sub}`));
    });
  });
}

// ── Reflections section ───────────────────────────────────────────────────

const _REF_PAGE = 30;

export function renderReflections(reflections, skillOutcomes) {
  const el = document.getElementById("memories-reflections");
  if (!el) return;

  const refs = Array.isArray(reflections) ? reflections : [];
  const outs = Array.isArray(skillOutcomes) ? skillOutcomes : [];

  _updateOvCount("ov-reflections-count", refs.length);

  const fmtTs = (epoch) => {
    const n = Number(epoch || 0);
    if (!n) return "";
    return new Date(n * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
  };
  const formatTaskFingerprint = (value) => {
    const raw = String(value || "general").trim();
    if (!raw) return "General Assistance";
    return raw
      .split("_")
      .filter(Boolean)
      .map(part => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  };

  if (!refs.length && !outs.length) {
    el.innerHTML = `
      <div class="mem-ref-hdr">
        <span class="mem-ref-label">Learning Reflections</span>
      </div>
      <div class="mem-section-empty">No reflections yet.<br>Generated automatically after tasks with 3 or more tool calls.</div>
    `;
    return;
  }

  const renderRefItems = (list) => list.map(r => {
    const ok = r.success ? "✓" : "✗";
    const qualClass = r.success ? "mem-ref-ok" : "mem-ref-fail";
    const dateStr = fmtTs(r.created);
    return `
      <div class="mem-ref-item">
        <span class="mem-ref-quality ${qualClass}">${ok}</span>
        <div class="mem-ref-body">
          <div class="mem-ref-lesson">${escHtml(r.lesson || r.outcome || "")}</div>
          ${r.failure_mode ? `<div class="mem-ref-failure">${escHtml(r.failure_mode)}</div>` : ""}
          <span class="mem-ref-task">Task Type: ${escHtml(formatTaskFingerprint(r.task_fingerprint))}</span>
          ${r.skill_name ? `<span class="mem-ref-skill">${escHtml(r.skill_name)}</span>` : ""}
          ${_renderSourceChip(r.source, { compact: true })}
        </div>
        ${dateStr ? `<span class="mem-ref-date">${escHtml(dateStr)}</span>` : ""}
      </div>
    `;
  }).join("");

  let _refOffset = Math.min(_REF_PAGE, refs.length);

  const outsHtml = outs.length ? `
    <div class="mem-ref-sub-hdr">Skill Outcomes</div>
    ${outs.map(o => {
      const score = Math.round((o.quality_score || 0) * 100);
      const scoreClass = score >= 80 ? "mem-sko-good" : score >= 50 ? "mem-sko-mid" : "mem-sko-poor";
      return `
        <div class="mem-sko-item">
          <span class="mem-sko-name">${escHtml(o.skill_name || "")}</span>
          <span class="mem-sko-score ${scoreClass}">${score}%</span>
          ${o.note ? `<span class="mem-sko-note">${escHtml(o.note)}</span>` : ""}
        </div>
      `;
    }).join("")}
  ` : "";

  const hasMore = refs.length > _REF_PAGE;
  el.innerHTML = `
    <div class="mem-ref-hdr">
      <span class="mem-ref-label">Learning Reflections</span>
      <span class="mem-ref-count">${refs.length}</span>
    </div>
    <div class="mem-ref-list" id="mem-ref-list-body">${renderRefItems(refs.slice(0, _refOffset))}</div>
    ${hasMore ? `<div class="load-more-row"><button class="secondary load-more-btn" id="mem-ref-load-more">More (${refs.length - _refOffset})</button></div>` : ""}
    ${outsHtml}
  `;

  if (hasMore) {
    document.getElementById("mem-ref-load-more")?.addEventListener("click", function () {
      const listEl = document.getElementById("mem-ref-list-body");
      if (!listEl) return;
      const next = refs.slice(_refOffset, _refOffset + _REF_PAGE);
      listEl.insertAdjacentHTML("beforeend", renderRefItems(next));
      _wireMemorySourceLinks(listEl);
      _refOffset += next.length;
      const remaining = refs.length - _refOffset;
      if (remaining <= 0) {
        this.closest(".load-more-row")?.remove();
      } else {
        this.textContent = `More (${remaining})`;
      }
    });
  }
  _wireMemorySourceLinks(el);
}

export function onMemoryDeleted(noteId, ok) {
  if (!ok) {
    showToast(`Failed to delete memory: ${noteId != null ? noteId : ""}`, "err");
    return;
  }
  // Re-fetch from offset 0 with current filter state
  send({ type: "get_memory_overview" });
  sendListMemories(_memQuery, 50, _memIncludeAuto, 0, _memKinds);
}
