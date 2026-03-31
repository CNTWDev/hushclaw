/**
 * events.js — UI state helpers, sendMessage, file uploads, all event listeners, and boot.
 */

import {
  state, wizard, agentsState, els, send, escHtml, setSending, markSessionRunning, getCurrentSessionId,
} from "./state.js";

import {
  insertUserMsg, insertSystemMsg, insertThinkingMsg, newSession,
} from "./chat.js";

import { openWizard, saveSettings, closeWizard } from "./settings.js";
import { switchTab, renderAgentsPanel, initSessionsSidebarState, toggleSessionsSidebar } from "./panels.js";
import { connect } from "./websocket.js";
import { initTheme } from "./theme.js";

// ── Textarea auto-resize ───────────────────────────────────────────────────

export function autoResize() {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(els.input.scrollHeight, 120) + "px";
}

// ── File upload / attachments ──────────────────────────────────────────────

export async function uploadFile(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      const b64      = reader.result.split(",")[1];
      const uploadId = Math.random().toString(36).slice(2);
      state._uploadPending.set(uploadId, resolve);
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({
          type: "file_upload",
          upload_id: uploadId,
          name: file.name,
          data: b64,
        }));
      } else {
        state._uploadPending.delete(uploadId);
        resolve({ ok: false, error: "Not connected" });
      }
    };
    reader.onerror = () => resolve({ ok: false, error: "FileReader error" });
    reader.readAsDataURL(file);
  });
}

export function renderAttachmentChips() {
  const chips = els.attachmentChips;
  if (!chips) return;
  chips.innerHTML = "";
  if (!state._attachments.length) {
    chips.classList.add("hidden");
    return;
  }
  chips.classList.remove("hidden");
  state._attachments.forEach((att, idx) => {
    const chip = document.createElement("div");
    chip.className = "attach-chip";
    chip.title = att.name;
    chip.innerHTML = `<span>📄 ${escHtml(att.name)}</span>`;
    const rm = document.createElement("button");
    rm.textContent = "✕";
    rm.title = "Remove";
    rm.addEventListener("click", () => {
      state._attachments.splice(idx, 1);
      renderAttachmentChips();
    });
    chip.appendChild(rm);
    chips.appendChild(chip);
  });
}

async function addFilesAsAttachments(files) {
  for (const file of files) {
    const result = await uploadFile(file);
    if (result.ok) {
      state._attachments.push({ file_id: result.file_id, name: result.name, url: result.url });
      renderAttachmentChips();
    } else {
      insertSystemMsg(`Upload failed: ${result.error || "unknown error"}`);
    }
  }
}

// ── @mention autocomplete ──────────────────────────────────────────────────

function _getMentionEl() {
  let el = document.getElementById("agent-mention-list");
  if (!el) {
    el = document.createElement("div");
    el.id = "agent-mention-list";
    el.className = "agent-mention-list hidden";
    const footer = document.querySelector("footer");
    const inputWrap = document.querySelector(".input-wrap");
    if (footer) footer.insertBefore(el, inputWrap || null);
  }
  return el;
}

function showAgentMentionList(query) {
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

function hideAgentMentionList() {
  state._mentionActive = false;
  state._mentionItems  = [];
  state._mentionIndex  = 0;
  const el = document.getElementById("agent-mention-list");
  if (el) el.classList.add("hidden");
}

function selectMentionAgent(name) {
  const val   = els.input.value;
  const atIdx = val.lastIndexOf("@");
  if (atIdx !== -1) {
    els.input.value = `${val.slice(0, atIdx)}@${name} `;
  }
  hideAgentMentionList();
  els.input.focus();
  autoResize();
}

function _currentMentionQuery() {
  const val   = els.input.value;
  const atIdx = val.lastIndexOf("@");
  return atIdx !== -1 ? val.slice(atIdx + 1) : "";
}

// ── Send message ───────────────────────────────────────────────────────────

export function sendMessage() {
  hideAgentMentionList();
  const rawText = els.input.value.trim();
  let text = rawText;
  if (!text || state.sending) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;

  const mentionPattern = /(^|\s)@([A-Za-z0-9_.-]+)\b/g;
  const mentionNames = [];
  let match = null;
  while ((match = mentionPattern.exec(rawText)) !== null) {
    mentionNames.push(match[2]);
  }
  const mentionTargets = [...new Set(mentionNames)];
  const knownNames = new Set((state.agents || []).map((a) => a.name));
  const unknownMentions = mentionTargets.filter((name) => !knownNames.has(name));
  const knownMentions = mentionTargets.filter((name) => knownNames.has(name));
  if (unknownMentions.length) {
    alert(`Unknown agent mention: ${unknownMentions.join(", ")}`);
    return;
  }
  if (knownMentions.length) {
    text = rawText
      .replace(mentionPattern, (_, prefix) => prefix)
      .replace(/\s{2,}/g, " ")
      .trim();
    if (!text) {
      alert("Please include task text after @agent mention.");
      return;
    }
  }

  state._toolBubbles        = {};
  state._toolPendingByName  = {};
  state._toolIndex          = 0;

  const attachments = state._attachments.slice();
  state._attachments = [];
  renderAttachmentChips();

  let displayText = els.input.value.trim();
  if (attachments.length) {
    displayText += (displayText ? "\n" : "") + attachments.map(a => `📎 ${a.name}`).join("\n");
  }
  insertUserMsg(displayText);
  els.input.value = "";
  autoResize();
  setSending(true);
  insertThinkingMsg();
  const currentSessionId = getCurrentSessionId();
  if (currentSessionId) markSessionRunning(currentSessionId, "thinking");

  const msg = knownMentions.length > 1
    ? {
        type: "broadcast_mention",
        text,
        agents: knownMentions,
        session_id: currentSessionId || undefined,
      }
    : {
        type: "chat",
        text,
        agent: knownMentions[0] || "default",
        session_id: currentSessionId || undefined,
      };
  if (attachments.length) msg.attachments = attachments;
  send(msg);
}

// ── Event listeners ────────────────────────────────────────────────────────

els.btnSend.addEventListener("click", sendMessage);

els.btnAttach?.addEventListener("click", () => els.fileInput?.click());

els.fileInput?.addEventListener("change", async () => {
  const files = Array.from(els.fileInput.files || []);
  if (!files.length) return;
  els.fileInput.value = "";
  await addFilesAsAttachments(files);
});

// Drag-and-drop file upload (same flow as click-to-upload)
let _dragDepth = 0;

function _hasDraggedFiles(ev) {
  return !!(ev.dataTransfer && Array.from(ev.dataTransfer.types || []).includes("Files"));
}

function _setDropActive(v) {
  if (!els.chatArea) return;
  els.chatArea.classList.toggle("drop-active", v);
}

function _preventBrowserFileOpen(ev) {
  if (!_hasDraggedFiles(ev)) return;
  ev.preventDefault();
}

document.addEventListener("dragover", _preventBrowserFileOpen);
document.addEventListener("drop", _preventBrowserFileOpen);
document.addEventListener("drop", () => {
  _dragDepth = 0;
  _setDropActive(false);
});

els.panelChat?.addEventListener("dragenter", (ev) => {
  if (!_hasDraggedFiles(ev)) return;
  ev.preventDefault();
  _dragDepth += 1;
  _setDropActive(true);
});

els.panelChat?.addEventListener("dragover", (ev) => {
  if (!_hasDraggedFiles(ev)) return;
  ev.preventDefault();
});

els.panelChat?.addEventListener("dragleave", (ev) => {
  if (!_hasDraggedFiles(ev)) return;
  ev.preventDefault();
  _dragDepth = Math.max(0, _dragDepth - 1);
  if (_dragDepth === 0) _setDropActive(false);
});

els.panelChat?.addEventListener("drop", async (ev) => {
  if (!_hasDraggedFiles(ev)) return;
  ev.preventDefault();
  _dragDepth = 0;
  _setDropActive(false);
  const files = Array.from(ev.dataTransfer?.files || []);
  if (!files.length) return;
  await addFilesAsAttachments(files);
});

els.btnStop.addEventListener("click", () => {
  const sid = getCurrentSessionId();
  if (!sid) return;
  send({ type: "stop", session_id: sid });
  setSending(false);
  insertSystemMsg("Task stopped.");
});

els.btnHandoverDone.addEventListener("click", () => {
  const sid = getCurrentSessionId();
  if (!sid) return;
  send({ type: "browser_handover_done", session_id: sid });
  els.handoverBanner.classList.add("hidden");
});

els.input.addEventListener("keydown", (ev) => {
  if (state._mentionActive) {
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      state._mentionIndex = (state._mentionIndex + 1) % state._mentionItems.length;
      showAgentMentionList(_currentMentionQuery());
      return;
    }
    if (ev.key === "ArrowUp") {
      ev.preventDefault();
      state._mentionIndex = (state._mentionIndex - 1 + state._mentionItems.length) % state._mentionItems.length;
      showAgentMentionList(_currentMentionQuery());
      return;
    }
    if (ev.key === "Tab" || (ev.key === "Enter" && !ev.shiftKey)) {
      ev.preventDefault();
      const item = state._mentionItems[state._mentionIndex];
      if (item) selectMentionAgent(item.name);
      return;
    }
    if (ev.key === "Escape") {
      hideAgentMentionList();
      return;
    }
  }
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); sendMessage(); }
});

els.input.addEventListener("input", () => {
  autoResize();
  const val   = els.input.value;
  const atIdx = val.lastIndexOf("@");
  if (atIdx !== -1 && (atIdx === 0 || /\s/.test(val[atIdx - 1]))) {
    const query = val.slice(atIdx + 1);
    if (!/\s/.test(query)) {
      showAgentMentionList(query);
      return;
    }
  }
  hideAgentMentionList();
});

els.btnNew.addEventListener("click", newSession);

els.agentSelect?.addEventListener("change", () => { state.agent = els.agentSelect.value; });

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

els.btnRefreshSess.addEventListener("click", () => send({ type: "list_sessions" }));
els.btnToggleSess?.addEventListener("click", () => toggleSessionsSidebar());
els.btnToggleSessInline?.addEventListener("click", () => toggleSessionsSidebar(false));

els.btnRefreshAgents?.addEventListener("click", () => send({ type: "list_agents" }));
els.btnAddAgent?.addEventListener("click", () => {
  agentsState.addingNew = true;
  renderAgentsPanel();
});
els.btnRunHierarchy?.addEventListener("click", () => {
  const commander = (prompt("Commander agent name:", "cmo") || "").trim();
  if (!commander) {
    alert("Commander is required.");
    return;
  }
  const task = prompt("Task for hierarchical execution:");
  if (!task || !task.trim()) return;
  const mode = (prompt("Mode: parallel or sequential", "parallel") || "parallel").trim().toLowerCase();
  send({
    type: "run_hierarchical",
    commander,
    text: task.trim(),
    mode: mode === "sequential" ? "sequential" : "parallel",
    session_id: getCurrentSessionId() || undefined,
  });
  setSending(true);
});

els.btnRefreshSkills?.addEventListener("click", () => {
  send({ type: "list_skills" });
  import("./panels.js").then(({ loadSkillMarketplace }) => loadSkillMarketplace());
});

els.btnRefreshMem.addEventListener("click", () => {
  els.memorySearch.value = "";
  send({ type: "list_memories", limit: 20 });
});

els.btnSearchMem.addEventListener("click", () => {
  const q = els.memorySearch.value.trim();
  send({ type: "list_memories", query: q, limit: 20 });
});

els.memorySearch.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    send({ type: "list_memories", query: els.memorySearch.value.trim(), limit: 20 });
  }
});

els.btnSettings.addEventListener("click", () => {
  if (!wizard.open) {
    wizard._pendingRefresh = true;
    openWizard(true);
  }
  send({ type: "get_config_status" });
});

els.wbtnSave.addEventListener("click", saveSettings);
els.wbtnClose.addEventListener("click", closeWizard);

// ── Boot ──────────────────────────────────────────────────────────────────

initTheme();
initSessionsSidebarState();
insertSystemMsg("Connecting to HushClaw…");
document.querySelector("#messages .msg:last-child").id = "msg-connecting";
connect();
