/**
 * stats.js — Lightweight chat-first workspace/session statistics.
 */

import { state, wizard, els, escHtml, getCurrentSessionId } from "./state.js";

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
  if (!els.chatStatsStrip) return;
  const hasActiveSession = Boolean(getCurrentSessionId());
  _stats.rounds = hasActiveSession ? _countRoundsFromDom() : _stats.workspaceRoundsLoaded;
  const items = [
    {
      label: "rounds",
      value: _formatCount(_stats.rounds, { plus: !hasActiveSession && _stats.sessionsHasMore }),
      hint: hasActiveSession ? "Current session user turns" : "Loaded workspace user turns",
    },
    { label: "sessions", value: _formatCount(_stats.sessionsLoaded, { plus: _stats.sessionsHasMore }), hint: "Loaded sessions in this workspace" },
    { label: "skills", value: _formatCount(_stats.skills), hint: "Available skills" },
    { label: "agents", value: _formatCount(_stats.agents), hint: "Configured agents" },
    { label: "model", value: _modelLabel(), hint: `Main model: ${wizard.model || "not configured"}`, wide: true },
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
