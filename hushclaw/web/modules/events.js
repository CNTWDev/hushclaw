/**
 * events.js — sendMessage, all event listeners, and boot.
 *
 * File upload / attachment logic lives in events/upload.js.
 * @mention and /slash autocomplete live in events/autocomplete.js.
 */

import {
  state, wizard, agentsState, skills, els, send, sendListMemories, setSending,
  markSessionRunning, getCurrentSessionId, updateState,
} from "./state.js";

import {
  insertUserMsg, insertSystemMsg, insertThinkingMsg, newSession, exportCurrentSessionAsPdf,
} from "./chat.js";

import { saveSettings, closeWizard } from "./settings.js";
import {
  switchTab, renderAgentsPanel, initSessionsSidebarState, toggleSessionsSidebar,
  runSessionSearch, clearSessionSearch, refreshSessionsView, selectedMemoryKinds,
  initFilesSidebar, initHtmlPreview, toggleFilesSidebar,
} from "./panels.js";
import { connect } from "./websocket.js";
import { initTheme } from "./theme.js";
import { initLocale, setLocale, currentLocale } from "./i18n.js";
import { openConfirm } from "./modal.js";

import {
  uploadFile, renderAttachmentChips, addFilesAsAttachments, extractPastedImages,
} from "./events/upload.js";
import {
  slashState, slashContextAtCursor,
  showSlashCommandList, hideSlashCommandList, selectSlashCommand,
  showAgentMentionList, hideAgentMentionList, selectMentionAgent, currentMentionQuery,
} from "./events/autocomplete.js";
import { consumeMessageReferences } from "./events/references.js";

export { uploadFile, renderAttachmentChips };

const LAST_TAB_KEY = "hushclaw.ui.last-tab";

function _tabFromHash() {
  const raw = String(location.hash || "").replace(/^#/, "");
  if (!raw) return "";
  if (raw.startsWith("tab=")) {
    const qs = new URLSearchParams(raw);
    return (qs.get("tab") || "").trim();
  }
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
  const knownMentions = mentionTargets.filter((name) => knownNames.has(name));
  // Unknown @names are kept as plain text — only valid agent mentions trigger routing.
  if (knownMentions.length) {
    text = rawText
      .replace(mentionPattern, (_, prefix, name) =>
        knownNames.has(name) ? prefix : `${prefix}@${name}`,
      )
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
  const references = consumeMessageReferences();

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
        client_now: new Date().toISOString(),
      }
    : {
        type: "chat",
        text,
        agent: knownMentions[0] || "default",
        session_id: currentSessionId || undefined,
        workspace: state.activeWorkspace || undefined,
        client_now: new Date().toISOString(),
      };
  if (attachments.length) msg.attachments = attachments;
  if (references.length) msg.references = references;
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

// Drag-and-drop file upload
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
  if (slashState.active) {
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      slashState.index = (slashState.index + 1) % slashState.items.length;
      const ctx = slashContextAtCursor();
      if (ctx) showSlashCommandList(ctx);
      return;
    }
    if (ev.key === "ArrowUp") {
      ev.preventDefault();
      slashState.index = (slashState.index - 1 + slashState.items.length) % slashState.items.length;
      const ctx = slashContextAtCursor();
      if (ctx) showSlashCommandList(ctx);
      return;
    }
    if (ev.key === "Tab" || (ev.key === "Enter" && !ev.shiftKey)) {
      ev.preventDefault();
      const item = slashState.items[slashState.index];
      if (item && item.available) selectSlashCommand(item.command);
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
      showAgentMentionList(currentMentionQuery());
      return;
    }
    if (ev.key === "ArrowUp") {
      ev.preventDefault();
      state._mentionIndex = (state._mentionIndex - 1 + state._mentionItems.length) % state._mentionItems.length;
      showAgentMentionList(currentMentionQuery());
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
  const slashCtx = slashContextAtCursor();
  if (slashCtx) {
    hideAgentMentionList();
    showSlashCommandList(slashCtx);
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
  const images = extractPastedImages(ev);
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

els.btnRefreshSess.addEventListener("click", () => refreshSessionsView());
els.btnSearchSess?.addEventListener("click", () => runSessionSearch(els.sessionSearch?.value || ""));
els.btnClearSessSearch?.addEventListener("click", () => clearSessionSearch());
els.sessionSearch?.addEventListener("keydown", (ev) => {
  if (ev.key !== "Enter" || ev.isComposing) return;
  ev.preventDefault();
  runSessionSearch(els.sessionSearch?.value || "");
});
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
  sendListMemories("", 50, includeAuto, 0, selectedMemoryKinds());
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
  sendListMemories(q, 50, includeAuto, 0, selectedMemoryKinds());
});

els.memorySearch.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    const includeAuto = document.getElementById("mem-show-auto")?.checked ?? false;
    sendListMemories(els.memorySearch.value.trim(), 50, includeAuto, 0, selectedMemoryKinds());
  }
});

document.getElementById("mem-show-auto")?.addEventListener("change", () => {
  const includeAuto = document.getElementById("mem-show-auto").checked;
  sendListMemories(els.memorySearch?.value?.trim() || "", 50, includeAuto, 0, selectedMemoryKinds());
});

document.getElementById("mem-kind-filter")?.addEventListener("change", () => {
  const includeAuto = document.getElementById("mem-show-auto")?.checked ?? false;
  sendListMemories(els.memorySearch?.value?.trim() || "", 50, includeAuto, 0, selectedMemoryKinds());
});

els.wbtnSave.addEventListener("click", saveSettings);
els.wbtnClose.addEventListener("click", closeWizard);

// ── Boot ──────────────────────────────────────────────────────────────────

initTheme();
initLocale();
document.getElementById("lang-toggle")?.addEventListener("click", () => {
  setLocale(currentLocale === "en" ? "zh" : "en");
});
initSessionsSidebarState();
initFilesSidebar();
document.getElementById("drawer-scrim")?.addEventListener("click", () => {
  if (!document.body.classList.contains("sessions-collapsed")) toggleSessionsSidebar(true);
  if (!document.body.classList.contains("files-sidebar-collapsed")) toggleFilesSidebar();
});
initHtmlPreview();
import("./calendar.js").then(({ initCalendar }) => initCalendar());
window.addEventListener("hashchange", _restoreTabFromUrlOrStorage);

// Open inline /files previews in a separate tab so the current WebUI session stays intact.
document.body.addEventListener("click", (ev) => {
  const link = ev.target.closest("a.dl-link");
  if (!link || link.hasAttribute("download")) return;
  let url;
  try {
    url = new URL(link.href, location.origin);
    if (!url.pathname.startsWith("/files/")) return;
  } catch {
    return;
  }
  ev.preventDefault();
  ev.stopPropagation();
  window.open(url.toString(), "_blank", "noopener,noreferrer");
});

// Restore upgrade-pending flag that may have been set before a page refresh
try {
  if (sessionStorage.getItem("hc_upgrade_pending") === "1") {
    updateState.expectingDisconnect = true;
    updateState.upgrading = true;
  }
} catch {}

insertSystemMsg("Connecting to HushClaw…");
document.querySelector("#messages .msg:last-child").id = "msg-connecting";

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
