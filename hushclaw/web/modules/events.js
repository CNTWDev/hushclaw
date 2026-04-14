/**
 * events.js — UI state helpers, sendMessage, file uploads, all event listeners, and boot.
 */

import {
  state, wizard, agentsState, skills, els, send, sendListMemories, escHtml, setSending, markSessionRunning, getCurrentSessionId,
} from "./state.js";

import {
  insertUserMsg, insertSystemMsg, insertThinkingMsg, newSession, exportCurrentSessionAsPdf,
} from "./chat.js";

import { saveSettings, closeWizard } from "./settings.js";
import { switchTab, renderAgentsPanel, initSessionsSidebarState, toggleSessionsSidebar } from "./panels.js";
import { connect } from "./websocket.js";
import { initTheme } from "./theme.js";
import { updateState } from "./state.js";
import { openConfirm } from "./modal.js";

const LAST_TAB_KEY = "hushclaw.ui.last-tab";

function _tabFromHash() {
  const raw = String(location.hash || "").replace(/^#/, "");
  if (!raw) return "";
  if (raw.startsWith("tab=")) {
    const qs = new URLSearchParams(raw);
    return (qs.get("tab") || "").trim();
  }
  // Backward-compatible form: "#forum"
  return decodeURIComponent(raw).trim();
}

function _restoreTabFromUrlOrStorage() {
  const fromHash = _tabFromHash();
  if (fromHash) {
    switchTab(fromHash);
    return;
  }
  try {
    const last = (localStorage.getItem(LAST_TAB_KEY) || "").trim();
    if (last) switchTab(last);
  } catch {
    // ignore storage errors
  }
}

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

const _IMAGE_EXTS = new Set(["jpg", "jpeg", "png", "gif", "webp", "bmp"]);
function _isImageFile(name) {
  const ext = (name || "").split(".").pop().toLowerCase();
  return _IMAGE_EXTS.has(ext);
}

function _extFromMime(type) {
  const t = String(type || "").toLowerCase();
  if (t.endsWith("/jpeg") || t.endsWith("/jpg")) return "jpg";
  if (t.endsWith("/png")) return "png";
  if (t.endsWith("/gif")) return "gif";
  if (t.endsWith("/webp")) return "webp";
  if (t.endsWith("/bmp")) return "bmp";
  return "png";
}

function _normalizePastedImage(file, index = 0) {
  if (!file) return null;
  const hasName = !!(file.name && file.name.trim());
  if (hasName && _isImageFile(file.name)) return file;
  const ext = _extFromMime(file.type);
  const ts = Date.now();
  const name = `pasted-image-${ts}-${index + 1}.${ext}`;
  try {
    return new File([file], name, {
      type: file.type || `image/${ext}`,
      lastModified: Date.now(),
    });
  } catch {
    return file;
  }
}

function _extractPastedImages(ev) {
  const dt = ev.clipboardData;
  if (!dt) return [];
  const out = [];
  const items = Array.from(dt.items || []);
  for (const item of items) {
    if (item.kind !== "file") continue;
    if (!String(item.type || "").toLowerCase().startsWith("image/")) continue;
    const f = item.getAsFile();
    if (f) out.push(f);
  }
  if (!out.length) {
    for (const f of Array.from(dt.files || [])) {
      if (String(f.type || "").toLowerCase().startsWith("image/")) out.push(f);
    }
  }
  return out.map((f, i) => _normalizePastedImage(f, i)).filter(Boolean);
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

    if (_isImageFile(att.name) && att.preview_url) {
      // Show thumbnail for image files
      const img = document.createElement("img");
      img.src = att.preview_url;
      img.className = "attach-chip-thumb";
      img.alt = att.name;
      chip.appendChild(img);
      const label = document.createElement("span");
      label.textContent = att.name;
      chip.appendChild(label);
    } else {
      chip.innerHTML = `<span>📄 ${escHtml(att.name)}</span>`;
    }

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
    // Generate a local preview URL for image files before uploading
    const previewUrl = _isImageFile(file.name) ? URL.createObjectURL(file) : null;
    const result = await uploadFile(file);
    if (result.ok) {
      state._attachments.push({
        file_id: result.file_id,
        name: result.name,
        url: result.url,
        preview_url: previewUrl,
      });
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

// ── Slash command autocomplete (/skills, /<skill>) ─────────────────────────

let _slashActive = false;
let _slashItems = [];
let _slashIndex = 0;

function _getSlashEl() {
  let el = document.getElementById("slash-command-list");
  if (!el) {
    el = document.createElement("div");
    el.id = "slash-command-list";
    el.className = "agent-mention-list hidden";
    const footer = document.querySelector("footer");
    const inputWrap = document.querySelector(".input-wrap");
    if (footer) footer.insertBefore(el, inputWrap || null);
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
    // Slash command parser currently supports one-token command names only.
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

function _slashContextAtCursor() {
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

function hideSlashCommandList() {
  _slashActive = false;
  _slashItems = [];
  _slashIndex = 0;
  const el = document.getElementById("slash-command-list");
  if (el) el.classList.add("hidden");
}

function _showSlashCommandList(ctx) {
  const q = ctx.query || "";
  const all = _buildSlashCatalog();
  const starts = all.filter((c) => c.command.slice(1).toLowerCase().startsWith(q));
  const contains = all.filter((c) => !starts.includes(c) && c.command.slice(1).toLowerCase().includes(q));
  const matches = [...starts, ...contains].slice(0, 12);
  if (!matches.length) {
    hideSlashCommandList();
    return;
  }
  _slashActive = true;
  _slashItems = matches;
  if (_slashIndex >= matches.length) _slashIndex = 0;

  const el = _getSlashEl();
  el.innerHTML = "";
  matches.forEach((c, i) => {
    const item = document.createElement("div");
    item.className = "mention-item" + (i === _slashIndex ? " active" : "") + (c.available ? "" : " mention-item-disabled");
    const reason = c.available ? "" : (c.reason || "Unavailable");
    item.innerHTML = `<span class="mention-name">${escHtml(c.command)}</span><span class="mention-desc">${escHtml(c.desc || reason)}</span>`;
    if (c.available) {
      item.addEventListener("mousedown", (ev) => {
        ev.preventDefault();
        _selectSlashCommand(c.command);
      });
    }
    el.appendChild(item);
  });
  el.classList.remove("hidden");
}

function _selectSlashCommand(command) {
  const ctx = _slashContextAtCursor();
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
  autoResize();
}

// ── Send message ───────────────────────────────────────────────────────────

export function sendMessage() {
  hideSlashCommandList();
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
  if (currentSessionId) markSessionRunning(currentSessionId, "thinking", true);

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
        workspace: state.activeWorkspace || undefined,
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
  if (_slashActive) {
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      _slashIndex = (_slashIndex + 1) % _slashItems.length;
      const ctx = _slashContextAtCursor();
      if (ctx) _showSlashCommandList(ctx);
      return;
    }
    if (ev.key === "ArrowUp") {
      ev.preventDefault();
      _slashIndex = (_slashIndex - 1 + _slashItems.length) % _slashItems.length;
      const ctx = _slashContextAtCursor();
      if (ctx) _showSlashCommandList(ctx);
      return;
    }
    if (ev.key === "Tab" || (ev.key === "Enter" && !ev.shiftKey)) {
      ev.preventDefault();
      const item = _slashItems[_slashIndex];
      if (item && item.available) _selectSlashCommand(item.command);
      return;
    }
    if (ev.key === "Escape") {
      hideSlashCommandList();
      return;
    }
  }
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
  const slashCtx = _slashContextAtCursor();
  if (slashCtx) {
    hideAgentMentionList();
    _showSlashCommandList(slashCtx);
    return;
  }
  hideSlashCommandList();
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

els.input.addEventListener("paste", async (ev) => {
  const images = _extractPastedImages(ev);
  if (!images.length) return;
  const hasText = Array.from(ev.clipboardData?.types || []).includes("text/plain");
  if (!hasText) ev.preventDefault();
  await addFilesAsAttachments(images);
});

els.btnNew.addEventListener("click", newSession);
els.btnExportPdf?.addEventListener("click", () => exportCurrentSessionAsPdf(els.btnExportPdf));

els.agentSelect?.addEventListener("change", () => { state.agent = els.agentSelect.value; });

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

els.btnRefreshSess.addEventListener("click", () => send({ type: "list_sessions", workspace: state.activeWorkspace || "" }));
els.btnToggleSess?.addEventListener("click", () => toggleSessionsSidebar());
els.btnToggleSessInline?.addEventListener("click", () => toggleSessionsSidebar(false));

els.btnRefreshAgents?.addEventListener("click", () => send({ type: "list_agents" }));
els.btnAddAgent?.addEventListener("click", () => {
  agentsState.addingNew = true;
  renderAgentsPanel();
});
function _commanderNames() {
  return (state.agents || [])
    .filter((a) => (a.role || "specialist") === "commander")
    .map((a) => a.name);
}

function _setHierarchyError(msg = "") {
  if (!els.hierarchyError) return;
  els.hierarchyError.textContent = msg;
  els.hierarchyError.classList.toggle("hidden", !msg);
}

function _openHierarchyRunner() {
  if (!els.hierarchyRunner) return;
  els.hierarchyRunner.classList.remove("hidden");
  _setHierarchyError("");
  const commanders = _commanderNames();
  if (els.hierarchyCommander) {
    const current = (els.hierarchyCommander.value || "").trim();
    if (!current && commanders.length) {
      els.hierarchyCommander.value = commanders[0];
    }
    els.hierarchyCommander.focus();
  }
}

function _closeHierarchyRunner() {
  if (!els.hierarchyRunner) return;
  els.hierarchyRunner.classList.add("hidden");
  _setHierarchyError("");
}

function _submitHierarchyRun() {
  const commander = (els.hierarchyCommander?.value || "").trim();
  const task = (els.hierarchyTask?.value || "").trim();
  const modeRaw = (els.hierarchyMode?.value || "parallel").trim().toLowerCase();
  const mode = modeRaw === "sequential" ? "sequential" : "parallel";
  const commanders = new Set(_commanderNames());

  if (!commander) {
    _setHierarchyError("Commander is required.");
    els.hierarchyCommander?.focus();
    return;
  }
  if (!commanders.has(commander)) {
    _setHierarchyError(`'${commander}' is not a commander agent.`);
    els.hierarchyCommander?.focus();
    return;
  }
  if (!task) {
    _setHierarchyError("Task is required.");
    els.hierarchyTask?.focus();
    return;
  }

  _setHierarchyError("");
  send({
    type: "run_hierarchical",
    commander,
    text: task,
    mode,
    session_id: getCurrentSessionId() || undefined,
  });
  setSending(true);
  _closeHierarchyRunner();
}

els.btnRunHierarchy?.addEventListener("click", () => {
  if (!els.hierarchyRunner) return;
  if (els.hierarchyRunner.classList.contains("hidden")) {
    _openHierarchyRunner();
  } else {
    _closeHierarchyRunner();
  }
});
els.btnRunHierarchySubmit?.addEventListener("click", _submitHierarchyRun);
els.btnRunHierarchyCancel?.addEventListener("click", _closeHierarchyRunner);
els.hierarchyTask?.addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") {
    ev.preventDefault();
    _submitHierarchyRun();
  }
});

els.btnRefreshSkills?.addEventListener("click", () => {
  send({ type: "list_skills" });
  import("./panels.js").then(({ loadSkillMarketplace }) => loadSkillMarketplace());
});

els.btnRefreshMem.addEventListener("click", () => {
  els.memorySearch.value = "";
  const includeAuto = document.getElementById("mem-show-auto")?.checked ?? false;
  sendListMemories("", 50, includeAuto, 0);
});

els.btnCompactMem?.addEventListener("click", async () => {
  const ok = await openConfirm({
    title: "Clean + Compact Memories",
    message:
      "Run one-click cleanup and compaction for auto memories?\n\n"
      + "- Deletes low-value auto notes\n"
      + "- Merges useful auto notes into daily summaries\n"
      + "- Keeps manual memories untouched",
    confirmText: "Run",
    cancelText: "Cancel",
    dangerConfirm: true,
  });
  if (!ok) return;
  send({ type: "compact_memories" });
});

els.btnSearchMem.addEventListener("click", () => {
  const q = els.memorySearch.value.trim();
  const includeAuto = document.getElementById("mem-show-auto")?.checked ?? false;
  sendListMemories(q, 50, includeAuto, 0);
});

els.memorySearch.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    const includeAuto = document.getElementById("mem-show-auto")?.checked ?? false;
    sendListMemories(els.memorySearch.value.trim(), 50, includeAuto, 0);
  }
});

document.getElementById("mem-show-auto")?.addEventListener("change", () => {
  const includeAuto = document.getElementById("mem-show-auto").checked;
  sendListMemories(els.memorySearch?.value?.trim() || "", 50, includeAuto, 0);
});

els.wbtnSave.addEventListener("click", saveSettings);
els.wbtnClose.addEventListener("click", closeWizard);

// ── Boot ──────────────────────────────────────────────────────────────────

initTheme();
initSessionsSidebarState();
window.addEventListener("hashchange", _restoreTabFromUrlOrStorage);

// Restore upgrade-pending flag that may have been set before a page refresh
// during an in-progress upgrade (sessionStorage survives page refresh but
// not tab close, which is exactly the behaviour we want here).
try {
  if (sessionStorage.getItem("hc_upgrade_pending") === "1") {
    updateState.expectingDisconnect = true;
    updateState.upgrading = true;
  }
} catch {}

insertSystemMsg("Connecting to HushClaw…");
document.querySelector("#messages .msg:last-child").id = "msg-connecting";

// Pre-compute the tab to restore but don't switch yet (WebSocket not ready)
// WebSocket onopen will restore it
const fromHash = _tabFromHash();
if (fromHash) {
  state._tabToRestorePending = fromHash;
} else {
  try {
    const last = (localStorage.getItem(LAST_TAB_KEY) || "").trim();
    if (last) state._tabToRestorePending = last;
  } catch {}
}

connect();
