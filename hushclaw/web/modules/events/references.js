/**
 * events/references.js — explicit message references for the next user turn.
 *
 * References are UI-selected opaque message ids. The browser never parses the
 * id shape; the server resolves it to the current storage backend.
 */

import { state, els, escHtml, showToast } from "../state.js";

const MAX_REFERENCES = 5;

function _ensureReferenceChips() {
  let el = document.getElementById("reference-chips");
  if (el) return el;
  el = document.createElement("div");
  el.id = "reference-chips";
  el.className = "reference-chips hidden";
  const attachmentChips = document.getElementById("attachment-chips");
  if (attachmentChips?.parentElement) {
    attachmentChips.parentElement.insertBefore(el, attachmentChips.nextSibling);
  } else {
    els.input?.closest("footer")?.insertBefore(el, els.input.closest(".input-wrap"));
  }
  return el;
}

export function renderReferenceChips() {
  const el = _ensureReferenceChips();
  const refs = state._messageReferences || [];
  el.classList.toggle("hidden", !refs.length);
  if (!refs.length) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = refs.map((ref, idx) => {
    const role = ref.role ? `${ref.role}: ` : "";
    const text = `${role}${ref.preview || ref.message_id || "message"}`.slice(0, 96);
    return `
      <span class="reference-chip" title="${escHtml(ref.message_id || "")}">
        <span class="reference-chip-label">引用</span>
        <span class="reference-chip-text">${escHtml(text)}</span>
        <button type="button" class="reference-chip-remove" data-idx="${idx}" title="Remove reference">×</button>
      </span>
    `;
  }).join("");
  el.querySelectorAll(".reference-chip-remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.dataset.idx || -1);
      if (idx >= 0) {
        state._messageReferences.splice(idx, 1);
        renderReferenceChips();
      }
    });
  });
}

export function addMessageReference({ message_id, role = "", preview = "" }) {
  const id = String(message_id || "").trim();
  if (!id) {
    showToast("This message cannot be referenced yet. Reload history after the turn finishes.", "info");
    return;
  }
  state._messageReferences = (state._messageReferences || []).filter((r) => r.message_id !== id);
  state._messageReferences.unshift({
    message_id: id,
    role,
    preview: String(preview || "").replace(/\s+/g, " ").trim(),
  });
  if (state._messageReferences.length > MAX_REFERENCES) {
    state._messageReferences = state._messageReferences.slice(0, MAX_REFERENCES);
  }
  renderReferenceChips();
  showToast("Referenced for next message", "info");
}

export function consumeMessageReferences() {
  const refs = (state._messageReferences || []).map((r) => ({ message_id: r.message_id }));
  state._messageReferences = [];
  renderReferenceChips();
  return refs;
}
