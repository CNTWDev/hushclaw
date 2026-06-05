/**
 * stats.js — Lightweight chat-first workspace/session statistics.
 */

import { state, els, escHtml } from "./state.js";

const _stats = {
  rounds: 0,
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

function _countRoundsFromDom() {
  if (!els.messages) return 0;
  return els.messages.querySelectorAll(".msg.user").length;
}

export function refreshChatStats() {
  if (!els.chatStatsStrip) return;
  _stats.rounds = _countRoundsFromDom();
  const items = [
    { label: "rounds", value: _formatCount(_stats.rounds), hint: "Current session user turns" },
    { label: "sessions", value: _formatCount(_stats.sessionsLoaded, { plus: _stats.sessionsHasMore }), hint: "Loaded sessions in this workspace" },
    { label: "skills", value: _formatCount(_stats.skills), hint: "Available skills" },
    { label: "agents", value: _formatCount(_stats.agents), hint: "Configured agents" },
    { label: "workspace", value: _workspaceLabel(), hint: "Active workspace", wide: true },
  ];
  els.chatStatsStrip.innerHTML = items.map(item => `
    <div class="chat-stat${item.wide ? " wide" : ""}" title="${escHtml(item.hint)}">
      <strong>${escHtml(item.value)}</strong>
      <span>${escHtml(item.label)}</span>
    </div>
  `).join("");
}

export function setSessionStats(items = [], hasMore = false, append = false) {
  const count = Array.isArray(items) ? items.length : 0;
  _stats.sessionsLoaded = append ? _stats.sessionsLoaded + count : count;
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
