/**
 * events/autocomplete.js — @mention and /slash-command autocomplete for the input textarea.
 *
 * Extracted from events.js. slashState is an exported mutable object so that
 * events.js can read .active/.items/.index without a circular dependency.
 */

import { state, els, escHtml, skills } from "../state.js";

const RECOMMENDATIONS_KEY = "hushclaw.ui.quick-recommendations";
const RECOMMENDATION_LIMIT = 8;
const RECOMMENDATION_KIND_LIMIT = 4;

// Private auto-resize (identical to autoResize() in events.js; inlined to avoid circularity)
function _autoResize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 120) + "px";
}

// ── @mention autocomplete ──────────────────────────────────────────────────

function _getMentionEl() {
  let el = document.getElementById("agent-mention-list");
  if (!el) {
    el = document.createElement("div");
    el.id = "agent-mention-list";
    el.className = "agent-mention-list hidden";
    const composer = document.getElementById("chat-composer");
    const inputWrap = document.querySelector(".input-wrap");
    if (composer) composer.insertBefore(el, inputWrap || null);
  }
  return el;
}

export function showAgentMentionList(query) {
  const q = query.toLowerCase();
  const matches = state.agents.filter(a => a.name.toLowerCase().startsWith(q));
  if (!matches.length) { hideAgentMentionList(); return; }

  state._mentionActive = true;
  state._mentionItems  = matches;
  if (state._mentionIndex >= matches.length) state._mentionIndex = 0;

  const el = _getMentionEl();
  el.innerHTML = "";
  matches.forEach((a, i) => {
    const item = document.createElement("div");
    item.className = "mention-item" + (i === state._mentionIndex ? " active" : "");
    item.innerHTML = `<span class="mention-name">@${a.name}</span>${a.description ? `<span class="mention-desc">${a.description}</span>` : ""}`;
    item.addEventListener("mousedown", (ev) => { ev.preventDefault(); selectMentionAgent(a.name); });
    el.appendChild(item);
  });
  el.classList.remove("hidden");
}

export function hideAgentMentionList() {
  state._mentionActive = false;
  state._mentionItems  = [];
  state._mentionIndex  = 0;
  const el = document.getElementById("agent-mention-list");
  if (el) el.classList.add("hidden");
}

export function selectMentionAgent(name) {
  const val   = els.input.value;
  const atIdx = val.lastIndexOf("@");
  if (atIdx !== -1) {
    els.input.value = `${val.slice(0, atIdx)}@${name} `;
  }
  hideAgentMentionList();
  _rememberRecommendation("agent", name);
  els.input.focus();
  _autoResize();
  refreshComposerRecommendations();
}

export function currentMentionQuery() {
  const val   = els.input.value;
  const atIdx = val.lastIndexOf("@");
  return atIdx !== -1 ? val.slice(atIdx + 1) : "";
}

function _agentMentionContextAtCursor() {
  const val = els.input.value || "";
  const cursor = els.input.selectionStart ?? val.length;
  const left = val.slice(0, cursor);
  const atIdx = left.lastIndexOf("@");
  if (atIdx === -1) return null;
  const prev = atIdx > 0 ? left[atIdx - 1] : "";
  if (atIdx !== 0 && prev && !/\s/.test(prev)) return null;
  const query = left.slice(atIdx + 1);
  if (/\s/.test(query)) return null;
  return { start: atIdx, end: cursor, query };
}

// ── /slash command autocomplete ────────────────────────────────────────────

/** Mutable state object — consumers read .active/.items/.index directly. */
export const slashState = { active: false, items: [], index: 0 };

function _getSlashEl() {
  let el = document.getElementById("slash-command-list");
  if (!el) {
    el = document.createElement("div");
    el.id = "slash-command-list";
    el.className = "agent-mention-list hidden";
    const composer = document.getElementById("chat-composer");
    const inputWrap = document.querySelector(".input-wrap");
    if (composer) composer.insertBefore(el, inputWrap || null);
  }
  return el;
}

function _buildSlashCatalog() {
  const cmdMap = new Map();
  cmdMap.set("/skills", {
    command: "/skills",
    desc: "List available skills.",
    available: true,
    reason: "",
    kind: "command",
    insertText: "/skills ",
  });
  const skillItems = skills.catalog?.length ? skills.catalog : (skills.installed || []);
  for (const s of skillItems) {
    const name = String(s?.name || "").trim();
    if (!name) continue;
    if (!/^[A-Za-z0-9_.-]+$/.test(name)) continue;
    const cmd = `/${name}`;
    if (cmdMap.has(cmd)) continue;
    cmdMap.set(cmd, {
      command: cmd,
      desc: s.description || "",
      available: s.available !== false,
      reason: s.reason || "",
      kind: "skill",
      insertText: `${cmd} `,
    });
  }
  return Array.from(cmdMap.values()).sort((a, b) => {
    if (a.command === "/skills") return -1;
    if (b.command === "/skills") return 1;
    return a.command.localeCompare(b.command);
  });
}

export function slashContextAtCursor() {
  const val = els.input.value || "";
  const cursor = els.input.selectionStart ?? val.length;
  const left = val.slice(0, cursor);
  const breakIdx = Math.max(left.lastIndexOf(" "), left.lastIndexOf("\n"), left.lastIndexOf("\t"));
  const tokenStart = breakIdx + 1;
  const token = left.slice(tokenStart);
  if (!token.startsWith("/")) return null;
  if (token.includes(" ")) return null;
  return {
    token,
    query: token.slice(1).toLowerCase(),
    start: tokenStart,
    end: cursor,
  };
}

export function hideSlashCommandList() {
  slashState.active = false;
  slashState.items = [];
  slashState.index = 0;
  const el = document.getElementById("slash-command-list");
  if (el) el.classList.add("hidden");
}

export function showSlashCommandList(ctx) {
  const q = ctx.query || "";
  const all = _buildSlashCatalog();
  const starts = all.filter((c) => c.command.slice(1).toLowerCase().startsWith(q));
  const contains = all.filter((c) => !starts.includes(c) && c.command.slice(1).toLowerCase().includes(q));
  const matches = [...starts, ...contains].slice(0, 12);
  if (!matches.length) {
    hideSlashCommandList();
    return;
  }
  slashState.active = true;
  slashState.items = matches;
  if (slashState.index >= matches.length) slashState.index = 0;

  const el = _getSlashEl();
  el.innerHTML = "";
  matches.forEach((c, i) => {
    const item = document.createElement("div");
    item.className = "mention-item" + (i === slashState.index ? " active" : "") + (c.available ? "" : " mention-item-disabled");
    const reason = c.available ? "" : (c.reason || "Unavailable");
    item.innerHTML = `<span class="mention-name">${escHtml(c.command)}</span><span class="mention-desc">${escHtml(c.desc || reason)}</span>`;
    if (c.available) {
      item.addEventListener("mousedown", (ev) => {
        ev.preventDefault();
        selectSlashCommand(c);
      });
    }
    el.appendChild(item);
  });
  el.classList.remove("hidden");
}

export function selectSlashCommand(itemOrCommand) {
  const item = typeof itemOrCommand === "string"
    ? { command: itemOrCommand, insertText: `${itemOrCommand} ` }
    : itemOrCommand;
  const insertText = item.insertText || `${item.command} `;
  const ctx = slashContextAtCursor();
  const val = els.input.value || "";
  if (!ctx) {
    els.input.value = insertText;
  } else {
    els.input.value = `${val.slice(0, ctx.start)}${insertText}${val.slice(ctx.end)}`;
    const pos = ctx.start + insertText.length;
    els.input.setSelectionRange(pos, pos);
  }
  hideSlashCommandList();
  if (item.kind === "skill") _rememberRecommendation("skill", item.command.replace(/^\//, ""));
  els.input.focus();
  _autoResize();
  refreshComposerRecommendations();
}

function _readRecommendations() {
  try {
    const value = JSON.parse(localStorage.getItem(RECOMMENDATIONS_KEY) || "[]");
    return Array.isArray(value) ? value.filter((item) => item && (item.kind === "skill" || item.kind === "agent") && item.name) : [];
  } catch {
    return [];
  }
}

function _rememberRecommendation(kind, name) {
  const normalized = String(name || "").trim().replace(/^\//, "").replace(/^@/, "");
  if (!normalized) return;
  const next = [{ kind, name: normalized, ts: Date.now() }, ..._readRecommendations()
    .filter((item) => !(item.kind === kind && item.name === normalized))].slice(0, 12);
  try { localStorage.setItem(RECOMMENDATIONS_KEY, JSON.stringify(next)); } catch { /* private mode */ }
}

function _recommendationItems() {
  const availableSkills = (skills.catalog?.length ? skills.catalog : (skills.installed || []))
    .map((item) => ({ kind: "skill", name: String(item?.name || "").trim(), available: item?.available !== false }))
    .filter((item) => item.name && /^[A-Za-z0-9_.-]+$/.test(item.name) && item.available);
  const availableAgents = (state.agents || [])
    .map((item) => ({ kind: "agent", name: String(item?.name || "").trim(), available: true }))
    .filter((item) => item.name);
  const all = [...availableSkills, ...availableAgents];
  const byKey = new Map(all.map((item) => [`${item.kind}:${item.name}`, item]));
  const recent = _readRecommendations()
    .sort((a, b) => (b.ts || 0) - (a.ts || 0))
    .map((item) => byKey.get(`${item.kind}:${item.name}`))
    .filter(Boolean);
  // Keep both routing modes discoverable. A long skill catalog must not hide agents.
  const selected = [];
  for (const kind of ["agent", "skill"]) {
    const recentOfKind = recent.filter((item) => item.kind === kind);
    const fallbackOfKind = all.filter((item) => item.kind === kind && !recentOfKind.some((recentItem) => recentItem.name === item.name));
    selected.push(...[...recentOfKind, ...fallbackOfKind].slice(0, RECOMMENDATION_KIND_LIMIT));
  }
  const selectedKeys = new Set(selected.map((item) => `${item.kind}:${item.name}`));
  const remainingRecent = recent.filter((item) => !selectedKeys.has(`${item.kind}:${item.name}`));
  return [...selected, ...remainingRecent].slice(0, RECOMMENDATION_LIMIT);
}

function _getRecommendationEl() {
  let el = document.getElementById("composer-recommendations");
  if (!el) {
    el = document.createElement("div");
    el.id = "composer-recommendations";
    el.className = "composer-recommendations hidden";
    el.setAttribute("aria-label", "Quick use");
    const composer = document.getElementById("chat-composer");
    const inputWrap = document.querySelector(".input-wrap");
    if (composer) composer.insertBefore(el, inputWrap || null);
  }
  return el;
}

export function insertQuickRecommendation(kind, name) {
  const value = els.input.value || "";
  const cursor = els.input.selectionStart ?? value.length;
  const insertText = `${kind === "agent" ? "@" : "/"}${name} `;
  els.input.value = `${value.slice(0, cursor)}${insertText}${value.slice(cursor)}`;
  const position = cursor + insertText.length;
  els.input.setSelectionRange(position, position);
  _rememberRecommendation(kind, name);
  hideSlashCommandList();
  hideAgentMentionList();
  els.input.focus();
  _autoResize();
  refreshComposerRecommendations();
}

export function refreshComposerRecommendations() {
  const el = _getRecommendationEl();
  const hasText = Boolean((els.input?.value || "").trim());
  const autocompleteActive = slashState.active || state._mentionActive;
  if (hasText || autocompleteActive) {
    el.classList.add("hidden");
    return;
  }
  const items = _recommendationItems();
  if (!items.length) {
    el.classList.add("hidden");
    return;
  }
  el.innerHTML = "";
  const label = document.createElement("span");
  label.className = "composer-recommendations-label";
  label.textContent = "Quick use";
  el.appendChild(label);
  items.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "composer-recommendation";
    button.dataset.kind = item.kind;
    button.textContent = `${item.kind === "agent" ? "@" : "/"}${item.name}`;
    button.setAttribute("aria-label", `Use ${item.kind} ${item.name}`);
    button.addEventListener("click", () => insertQuickRecommendation(item.kind, item.name));
    el.appendChild(button);
  });
  el.classList.remove("hidden");
}

export function refreshComposerAutocomplete() {
  const slashCtx = slashContextAtCursor();
  if (slashCtx) {
    hideAgentMentionList();
    showSlashCommandList(slashCtx);
    refreshComposerRecommendations();
    return;
  }
  hideSlashCommandList();

  const mentionCtx = _agentMentionContextAtCursor();
  if (mentionCtx) {
    showAgentMentionList(mentionCtx.query);
    refreshComposerRecommendations();
    return;
  }
  hideAgentMentionList();
  refreshComposerRecommendations();
}
