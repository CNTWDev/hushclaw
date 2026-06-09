/**
 * stats.js — Lightweight chat-first workspace/session statistics.
 */

import { state, wizard, els, escHtml, getCurrentSessionId, getCurrentSessionTitle } from "./state.js";

const _stats = {
  rounds: 0,
  workspaceRoundsLoaded: 0,
  sessionsLoaded: 0,
  sessionsHasMore: false,
  skills: null,
  agents: null,
};

function _formatCount(value, { plus = false } = {}) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const n = Math.max(0, Number(value));
  return `${n.toLocaleString()}${plus ? "+" : ""}`;
}

function _workspaceLabel() {
  return state.activeWorkspace || "Default";
}

function _modelLabel() {
  const model = String(wizard.model || "").trim();
  if (!model) return "—";
  const parts = model.split("/").filter(Boolean);
  return parts[parts.length - 1] || model;
}

function _countRoundsFromDom() {
  if (!els.messages) return 0;
  return els.messages.querySelectorAll(".msg.user").length;
}

export function refreshChatStats() {
  const root = els.chatContextMeta || els.chatStatsStrip;
  if (!root) return;
  const hasActiveSession = Boolean(getCurrentSessionId());
  _stats.rounds = hasActiveSession ? _countRoundsFromDom() : _stats.workspaceRoundsLoaded;
  const rounds = _formatCount(_stats.rounds, { plus: !hasActiveSession && _stats.sessionsHasMore });
  if (hasActiveSession) {
    const title = getCurrentSessionTitle() || "Session";
    const meta = [
      { value: _modelLabel(), label: "model", hint: `Main model: ${wizard.model || "not configured"}` },
      { value: rounds, label: "turns", hint: "Current session user turns" },
      { value: _workspaceLabel(), label: "workspace", hint: "Active workspace" },
    ];
    root.innerHTML = `
      <div class="chat-session-heading">
        <div class="chat-session-title" title="${escHtml(title)}">${escHtml(title)}</div>
        <div class="chat-session-meta">
          ${meta.map(item => `
            <span class="chat-session-meta-item" title="${escHtml(item.hint)}">
              <strong>${escHtml(item.value)}</strong>
              <span>${escHtml(item.label)}</span>
            </span>
          `).join("")}
        </div>
      </div>
    `;
    return;
  }

  const sessions = _formatCount(_stats.sessionsLoaded, { plus: _stats.sessionsHasMore });
  const items = [
    { label: "workspace", value: _workspaceLabel(), hint: "Active workspace", primary: true },
    { label: "model", value: _modelLabel(), hint: `Main model: ${wizard.model || "not configured"}`, primary: true },
    { label: "sessions", value: sessions, hint: "Loaded sessions in this workspace", secondary: true },
  ];
  root.innerHTML = items.map(item => `
    <span class="chat-context-item${item.primary ? " is-primary" : ""}${item.secondary ? " is-secondary" : ""}" title="${escHtml(item.hint)}">
      <strong>${escHtml(item.value)}</strong>
      <span>${escHtml(item.label)}</span>
    </span>
  `).join("");
}

export function setSessionStats(items = [], hasMore = false, append = false) {
  const list = Array.isArray(items) ? items : [];
  const count = list.length;
  const rounds = list.reduce((total, item) => {
    const n = Number(item?.turn_count || 0);
    return total + (Number.isFinite(n) && n > 0 ? n : 0);
  }, 0);
  _stats.sessionsLoaded = append ? _stats.sessionsLoaded + count : count;
  _stats.workspaceRoundsLoaded = append ? _stats.workspaceRoundsLoaded + rounds : rounds;
  _stats.sessionsHasMore = Boolean(hasMore);
  refreshChatStats();
}

export function setSkillStats(data = {}) {
  const items = Array.isArray(data.items) ? data.items : [];
  _stats.skills = Number(data.total ?? items.length);
  refreshChatStats();
}

export function setAgentStats(items = []) {
  _stats.agents = Array.isArray(items) ? items.length : 0;
  refreshChatStats();
}
