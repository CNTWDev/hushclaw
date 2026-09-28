/**
 * chat/turn-changes.js — Per-turn result card: what this answer changed.
 *
 * Shows memories saved, skills evolved and files written during one assistant
 * turn, with a way to undo a memory or open what changed. Data comes from the
 * `changes` list on the `done` event and on replayed assistant history turns.
 */
import { state } from "../state.js";
import { uiText } from "../i18n.js";
import { resolveFileUrl } from "../http.js";

const VISIBLE_ROWS = 3;
const ARM_MS = 3000;
const KIND_ORDER = ["memory", "skill", "state", "file"];
const pendingUndo = new Map(); // note_id -> row element

function node(tag, className = "", text = "") {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text) el.textContent = text;
  return el;
}

function kindLabel(kind) {
  return {
    memory: uiText("Memory"),
    skill: uiText("Skill"),
    state: uiText("Goals"),
    file: uiText("File"),
  }[kind] || kind;
}

function summaryText(changes) {
  const counts = {};
  for (const c of changes) counts[c.kind] = (counts[c.kind] || 0) + 1;
  const parts = [];
  if (counts.memory) parts.push(uiText("Remembered {n}", { n: counts.memory }));
  if (counts.skill) parts.push(uiText("{n} skills updated", { n: counts.skill }));
  if (counts.state) parts.push(uiText("Goals updated"));
  if (counts.file) parts.push(uiText("{n} files changed", { n: counts.file }));
  return parts.join(" · ");
}

function markRemoved(row, text) {
  row.classList.add("is-removed");
  const action = row.querySelector(".turn-change-action");
  if (action) {
    const note = node("span", "turn-change-status", text);
    action.replaceWith(note);
  }
}

function undoButton(row, change) {
  const btn = node("button", "turn-change-action", uiText("Undo"));
  btn.type = "button";
  btn.title = uiText("Forget this memory");
  let armTimer = null;
  btn.addEventListener("click", () => {
    const noteId = change.ref?.note_id;
    if (!noteId || state.ws?.readyState !== 1) return;
    if (!btn.classList.contains("is-armed")) {
      // Deleting a memory cannot be reversed, so ask for a second click.
      btn.classList.add("is-armed");
      btn.textContent = uiText("Confirm undo");
      armTimer = setTimeout(() => {
        btn.classList.remove("is-armed");
        btn.textContent = uiText("Undo");
      }, ARM_MS);
      return;
    }
    clearTimeout(armTimer);
    btn.disabled = true;
    pendingUndo.set(noteId, row);
    state.ws.send(JSON.stringify({ type: "delete_memory", note_id: noteId }));
  });
  return btn;
}

function openButton(change) {
  const url = change.ref?.url;
  if (!url) return null;
  const apiKey = new URLSearchParams(location.search).get("api_key") || "";
  const link = node("a", "turn-change-action", uiText("Open"));
  link.href = resolveFileUrl(url, apiKey);
  link.target = "_blank";
  link.rel = "noopener";
  return link;
}

function skillButton() {
  const btn = node("button", "turn-change-action", uiText("View"));
  btn.type = "button";
  btn.addEventListener("click", () => {
    import("../panels/agents.js").then(({ switchTab }) => switchTab("skills"));
  });
  return btn;
}

function renderRow(change) {
  const row = node("li", `turn-change-row kind-${change.kind || "other"}`);
  row.append(node("span", "turn-change-kind", kindLabel(change.kind)));
  const text = node("div", "turn-change-text");
  text.append(node("div", "turn-change-title", change.title || ""));
  if (change.detail && change.detail !== change.title) {
    text.append(node("div", "turn-change-detail", change.detail));
  }
  row.append(text);
  let action = null;
  if (change.kind === "memory" && change.ref?.note_id) action = undoButton(row, change);
  else if (change.kind === "file") action = openButton(change);
  else if (change.kind === "skill") action = skillButton();
  if (action) row.append(action);
  if (change.removed) markRemoved(row, uiText("Forgotten"));
  return row;
}

export function attachTurnChanges(message, changes) {
  if (!message || !Array.isArray(changes) || !changes.length) return;
  const items = changes
    .filter(c => c && c.kind)
    .sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind));
  if (!items.length) return;
  message.querySelector(".turn-changes-card")?.remove();

  const card = node("section", "turn-changes-card");
  const head = node("div", "turn-changes-head");
  head.append(node("span", "turn-changes-icon", "✦"));
  const headText = node("div", "turn-changes-head-text");
  headText.append(node("div", "turn-changes-title", uiText("This turn")));
  headText.append(node("div", "turn-changes-summary", summaryText(items)));
  head.append(headText);
  card.append(head);

  const list = node("ul", "turn-changes-list");
  items.forEach((change, i) => {
    const row = renderRow(change);
    if (i >= VISIBLE_ROWS) row.hidden = true;
    list.append(row);
  });
  card.append(list);

  if (items.length > VISIBLE_ROWS) {
    const more = node("button", "turn-changes-more",
      uiText("Show {n} more", { n: items.length - VISIBLE_ROWS }));
    more.type = "button";
    more.addEventListener("click", () => {
      list.querySelectorAll(".turn-change-row[hidden]").forEach(r => { r.hidden = false; });
      more.remove();
    });
    card.append(more);
  }

  const content = message.querySelector(".msg-content") || message;
  const footer = content.querySelector(".msg-actions-footer");
  content.insertBefore(card, footer || null);
}

/** Called for every `memory_deleted` event; only rows this card asked about react. */
export function receiveTurnChangeUndo(data) {
  const row = pendingUndo.get(data?.note_id);
  if (!row) return;
  pendingUndo.delete(data.note_id);
  if (data.ok) {
    markRemoved(row, uiText("Forgotten"));
  } else {
    const btn = row.querySelector(".turn-change-action");
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("is-armed");
      btn.textContent = uiText("Undo");
    }
  }
}
