/**
 * events/autocomplete.js — @mention and /slash-command autocomplete for the input textarea.
 *
 * Extracted from events.js. slashState is an exported mutable object so that
 * events.js can read .active/.items/.index without a circular dependency.
 */

import { state, els, escHtml, skills } from "../state.js";

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
  els.input.focus();
  _autoResize();
}

export function currentMentionQuery() {
  const val   = els.input.value;
  const atIdx = val.lastIndexOf("@");
  return atIdx !== -1 ? val.slice(atIdx + 1) : "";
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
  });
  for (const s of (skills.installed || [])) {
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
        selectSlashCommand(c.command);
      });
    }
    el.appendChild(item);
  });
  el.classList.remove("hidden");
}

export function selectSlashCommand(command) {
  const ctx = slashContextAtCursor();
  const val = els.input.value || "";
  if (!ctx) {
    els.input.value = `${command} `;
  } else {
    els.input.value = `${val.slice(0, ctx.start)}${command} ${val.slice(ctx.end)}`;
    const pos = ctx.start + command.length + 1;
    els.input.setSelectionRange(pos, pos);
  }
  hideSlashCommandList();
  els.input.focus();
  _autoResize();
}
