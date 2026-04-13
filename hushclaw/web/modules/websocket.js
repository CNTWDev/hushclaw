/**
 * websocket.js — WebSocket connection lifecycle and message dispatcher.
 */

import {
  state, wizard, els, updateState,
  send, sendListMemories, memoriesListRequestGen, setConnStatus, showToast, updateTokenStats, setSending,
  markSessionRunning, markSessionIdle, setSessionStatus, getSessionStatus,
  getCurrentSessionId, setCurrentSessionId, debugUiLifecycle,
} from "./state.js";

import {
  appendChunk, setChunkText, finalizeAiMsg, insertSystemMsg, insertErrorMsg,
  insertToolBubble, updateToolBubble, renderSessionHistory, rehydrateInProgressUi,
  insertRoundLine,
} from "./chat.js";

import {
  handleConfigStatus, handleConfigSaved, openWizard,
  handleModelsResponse, handleTestProviderStep, handleTestProviderResult,
  handleTransssionCodeSent, handleTransssionAuthed, handleTransssionQuotaResult,
  resetTranssionPendingUi,
  resetWizardTimers,
} from "./settings.js";

import {
  populateAgents, renderAgentsPanel, handleAgentDetail,
  renderSessions, renderMemories, onMemoryDeleted, onSessionDeleted,
  handleSkillsList, handleSkillRepos, handleSkillInstallResult,
  handleSkillSaved, handleSkillDeleted, handleSkillExportReady, handleSkillImportResult,
  switchTab, renderWorkspaceSelector,
} from "./panels.js";

import {
  renderTodos, onTodoCreated, onTodoUpdated, onTodoDeleted,
  renderScheduledTasks, onTaskCreated, onTaskToggled,
} from "./tasks.js";
import {
  handleUpdateStatus, handleUpdateAvailable, handleUpdateProgress, handleUpdateResult,
  handleServerShutdown, refreshUpdateUi, requestCheckUpdate,
} from "./updates.js";

// ── WebSocket URL ──────────────────────────────────────────────────────────

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const host  = location.host || "127.0.0.1:8765";
  const params = new URLSearchParams(location.search);
  const key    = params.get("api_key") || "";
  const q      = key ? `?api_key=${encodeURIComponent(key)}` : "";
  return `${proto}//${host}${q}`;
}

// ── Startup overlay helpers ────────────────────────────────────────────────

function hideStartupOverlay() {
  const el = document.getElementById("startup-overlay");
  if (!el || el.classList.contains("hidden")) return;
  el.classList.add("fade-out");
  setTimeout(() => el.classList.add("hidden"), 380);
}

function updateStartupStatus() {
  const n = state._reconnectAttempts;
  const statusEl = document.getElementById("startup-status");
  const hintEl   = document.getElementById("startup-hint");
  if (!statusEl) return;
  if (n === 0) {
    statusEl.textContent = "Connecting to server…";
    statusEl.className = "startup-status";
  } else if (n === 1) {
    statusEl.textContent = "Retrying connection…";
    statusEl.className = "startup-status";
  } else {
    statusEl.textContent = `Still waiting… (attempt ${n + 1})`;
    statusEl.className = "startup-status warn";
  }
  if (hintEl) {
    if (n >= 4) {
      hintEl.textContent = "Server may be taking longer than usual to start. Keep waiting or check your terminal.";
    } else if (n >= 2) {
      hintEl.textContent = "Server is starting up — almost there.";
    } else {
      hintEl.textContent = "Server is starting up, this usually takes a few seconds.";
    }
  }
}

// ── Reconnect banner helpers ───────────────────────────────────────────────

function showReconnectBanner() {
  const el = document.getElementById("reconnect-banner");
  if (el) el.classList.remove("hidden");
}

function hideReconnectBanner() {
  const el = document.getElementById("reconnect-banner");
  if (el) el.classList.add("hidden");
  // Clear any pending countdown tick
  if (state._reconnectCountdownTimer) {
    clearInterval(state._reconnectCountdownTimer);
    state._reconnectCountdownTimer = null;
  }
  const cd = document.getElementById("reconnect-countdown");
  if (cd) cd.textContent = "";
}

function updateReconnectMsg(msg) {
  const el = document.getElementById("reconnect-msg");
  if (el) el.textContent = msg;
}

function startCountdown(seconds) {
  if (state._reconnectCountdownTimer) {
    clearInterval(state._reconnectCountdownTimer);
    state._reconnectCountdownTimer = null;
  }
  const cd = document.getElementById("reconnect-countdown");
  if (!cd) return;
  let remaining = seconds;
  cd.textContent = `retry in ${remaining}s`;
  state._reconnectCountdownTimer = setInterval(() => {
    remaining--;
    if (remaining <= 0) {
      cd.textContent = "connecting…";
      clearInterval(state._reconnectCountdownTimer);
      state._reconnectCountdownTimer = null;
    } else {
      cd.textContent = `retry in ${remaining}s`;
    }
  }, 1000);
}

// ── Connection ─────────────────────────────────────────────────────────────

export function connect() {
  if (state.ws && state.ws.readyState <= WebSocket.OPEN) return;

  if (state._isInitialConnect) {
    // During initial startup: update overlay text, don't touch the dot yet
    updateStartupStatus();
  } else {
    setConnStatus("reconnecting");
    showReconnectBanner();
    updateReconnectMsg("Connection lost — reconnecting…");
  }

  let ws;
  try {
    ws = new WebSocket(wsUrl());
  } catch (err) {
    state._reconnectAttempts++;
    if (state._isInitialConnect) {
      updateStartupStatus();
    } else {
      setConnStatus("disconnected");
      insertErrorMsg(`WebSocket init failed: ${String(err)}`);
    }
    scheduleReconnect();
    return;
  }
  state.ws = ws;

  ws.onopen = () => {
    setConnStatus("connected");
    state._reconnectDelay = 1000;
    state._reconnectAttempts = 0;
    els.btnSend.disabled = false;
    document.getElementById("msg-connecting")?.remove();

    if (state._isInitialConnect) {
      state._isInitialConnect = false;
      hideStartupOverlay();
    } else {
      hideReconnectBanner();
    }

    // If the connection dropped during an in-progress upgrade, the upgrade
    // script killed the old server process (expected).  Don't declare success
    // immediately — request a version check and let handleUpdateStatus compare
    // the new version against the one we had before triggering the upgrade.
    if (updateState.expectingDisconnect) {
      updateState.upgrading        = false;
      updateState.checking         = false;
      updateState.expectingDisconnect = false;
      updateState.verifyingUpgrade = true;
      try { sessionStorage.removeItem("hc_upgrade_pending"); } catch {}
      refreshUpdateUi();
      insertSystemMsg("Reconnected — verifying upgrade…");
      requestCheckUpdate(true);  // handleUpdateStatus will resolve the outcome
    }

    // On initial connect, restore any pending tab (must be after ws is ready)
    if (state._tabToRestorePending) {
      const tabToRestore = state._tabToRestorePending;
      state._tabToRestorePending = null;
      switchTab(tabToRestore);
    }

    send({ type: "list_agents" });
    send({ type: "list_sessions", workspace: state.activeWorkspace || "" });
    send({ type: "list_skills" });
    send({ type: "list_todos" });
    send({ type: "list_scheduled_tasks" });
    const sid = getCurrentSessionId();
    if (sid) {
      setSessionStatus(sid, "stale", "reconnect_sync", "waiting");
      // Try to subscribe to a still-running session first; fall back to history.
      send({ type: "subscribe", session_id: sid });
      send({ type: "get_session_history", session_id: sid });
    }

    resetWizardTimers();

    // Reset wizard save-in-progress UI if the connection dropped mid-save.
    if (wizard.open && wizard.saving) {
      wizard.saving = false;
      els.wbtnSave.disabled = false;
      els.wbtnSave.textContent = "💾 Save";
      els.wstatus.textContent = "✗ Connection lost — please try again.";
      els.wstatus.className = "wstatus err";
    }

    const testBtn = document.getElementById("wiz-test-btn");
    if (testBtn && testBtn.disabled) {
      testBtn.disabled = false;
      testBtn.textContent = "Test Connection";
      const c = document.getElementById("wiz-test-steps");
      if (c) {
        const s = document.createElement("div");
        s.className = "test-step-summary error";
        s.textContent = "✗ WebSocket connection lost — please retry.";
        c.appendChild(s);
      }
    }
  };

  ws.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch { return; }
    handleMessage(data);
  };

  ws.onclose = (ev) => {
    setConnStatus("disconnected");
    els.btnSend.disabled = true;
    const wasFirstDisconnect = state._reconnectAttempts === 0;
    state._reconnectAttempts++;
    const sid = getCurrentSessionId();
    if (sid && getSessionStatus(sid) === "running") {
      setSessionStatus(sid, "offline", "disconnect", "offline");
      setSending(false);
      rehydrateInProgressUi(sid);
    }
    if (state._isInitialConnect) {
      // Quiet during startup — overlay already shows status
      updateStartupStatus();
    } else if (wasFirstDisconnect) {
      // Only show disconnect message once; subsequent retry failures are silent
      // (the reconnect banner + countdown provides feedback instead)
      const reason = ev && ev.reason ? ` (${ev.reason})` : "";
      insertSystemMsg(`Disconnected: code ${ev.code}${reason}`);
    }
    scheduleReconnect();
  };

  // onerror always precedes onclose — let onclose handle all UI updates
  ws.onerror = () => { ws.close(); };
}

export function scheduleReconnect() {
  if (state._reconnectTimer) return;
  const delay = state._reconnectDelay;
  state._reconnectDelay = Math.min(delay * 2, 30000);

  // Drive countdown in the reconnect banner (only when not in initial startup)
  if (!state._isInitialConnect) {
    startCountdown(Math.ceil(delay / 1000));
  }

  state._reconnectTimer = setTimeout(() => {
    state._reconnectTimer = null;
    connect();
  }, delay);
}

function applySessionStatus(data) {
  const sid = data.session_id || getCurrentSessionId();
  if (!sid) return;
  const status = data.status || "idle";
  const reason = data.reason || "unknown";
  setSessionStatus(sid, status, reason, status === "running" ? "thinking" : status, data.ts || Date.now());
  debugUiLifecycle("session_status", { session_id: sid, status, reason, tab: state.tab });
  if (sid === getCurrentSessionId()) {
    if (status === "running") {
      setSending(true);
      rehydrateInProgressUi(sid);
    } else if (status === "idle" || status === "offline" || status === "stale") {
      setSending(false);
      rehydrateInProgressUi(sid);
      if (status === "offline") {
        insertSystemMsg("Connection lost. Reconnecting…");
      } else if (status === "stale") {
        insertSystemMsg("Reconnected. Syncing session status…");
      }
    }
  }
}

// ── Message dispatcher ─────────────────────────────────────────────────────

export function handleMessage(data) {
  switch (data.type) {
    case "file_uploaded": {
      const resolve = state._uploadPending.get(data.upload_id);
      if (resolve) {
        state._uploadPending.delete(data.upload_id);
        resolve(data);
      }
      break;
    }
    case "config_status":
      handleConfigStatus(data);
      // Update workspace selector in sidebar
      renderWorkspaceSelector(data.workspaces || []);
      // Refresh status dots if the channels panel is visible
      if (state.tab === "channels") {
        import("./channels.js").then(({ updateChannelStatusDots }) => updateChannelStatusDots());
      }
      break;
    case "config_saved":
      handleConfigSaved(data);
      break;
    case "workspace_initialized": {
      const initBtn = document.getElementById("mem-init-workspace-btn");
      const statusEl = document.getElementById("mem-init-ws-status");
      if (initBtn) initBtn.disabled = false;
      if (data.ok) {
        if (statusEl) statusEl.textContent = `✓ Workspace ready at ${data.path}`;
        // Refresh config status so the UI shows updated badges
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
          state.ws.send(JSON.stringify({ type: "get_config_status" }));
        }
      } else {
        if (statusEl) statusEl.textContent = `✗ ${data.error || "Failed"}`;
      }
      break;
    }
    case "config_reloaded":
      showToast("Config reloaded from file", "info");
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: "get_config_status" }));
      }
      break;
    case "session":
      setCurrentSessionId(data.session_id);
      if (state.sending) markSessionRunning(data.session_id, "thinking", true);
      break;
    case "session_status":
      applySessionStatus(data);
      break;
    case "chunk":
      if (getCurrentSessionId()) markSessionRunning(getCurrentSessionId(), "streaming");
      if (data.text) {
        if (data._replay) {
          // Full accumulated text delivered by the server after a reconnect:
          // replace the current in-progress bubble rather than appending.
          setChunkText(data.text);
        } else {
          appendChunk(data.text);
        }
      }
      break;
    case "tool_call":
      if (getCurrentSessionId()) markSessionRunning(getCurrentSessionId(), "tooling");
      insertToolBubble(data);
      break;
    case "round_info":
      insertRoundLine(data.round, data.max_rounds || 0);
      if (getCurrentSessionId()) markSessionRunning(getCurrentSessionId(), "thinking");
      break;
    case "tool_result":
      updateToolBubble(data);
      if (data.tool === "remember_skill") {
        send({ type: "list_skills" });
      }
      if (data.tool === "add_todo" || data.tool === "complete_todo") {
        send({ type: "list_todos" });
      }
      if (data.tool === "browser_open_for_user" && !data.is_error) {
        els.handoverBanner.classList.remove("hidden");
        els.handoverMsg.textContent =
          "🔐 Browser window opened — complete your action, then click Done";
      }
      if (data.tool === "browser_wait_for_user") {
        els.handoverBanner.classList.add("hidden");
      }
      break;
    case "stopped":
      debugUiLifecycle("session_stopped", { session_id: getCurrentSessionId(), tab: state.tab });
      if (getCurrentSessionId()) markSessionIdle(getCurrentSessionId());
      finalizeAiMsg();
      setSending(false);
      break;
    case "replay_start": {
      // A running session was found; clear any stale in-progress UI and get ready
      // to receive replayed structural events followed by live stream continuation.
      const rSid = data.session_id;
      if (rSid && rSid === getCurrentSessionId()) {
        finalizeAiMsg();
        setSending(true);
        setSessionStatus(rSid, "running", "reconnected", "thinking");
      }
      break;
    }
    case "replay_end": {
      // Replay complete — live events will continue from here.
      const rSid = data.session_id;
      if (rSid && rSid === getCurrentSessionId()) {
        insertSystemMsg("↩ Reconnected — resuming session…");
      }
      break;
    }
    case "session_not_running":
      // Session has already finished or expired from memory; history already
      // handles the final state via get_session_history.
      break;
    case "session_history":
      renderSessionHistory(data.session_id, data.turns || []);
      if (data.session_id === getCurrentSessionId() && getSessionStatus(data.session_id) === "stale") {
        const turns = data.turns || [];
        const lastRole = turns.length ? String(turns[turns.length - 1]?.role || "") : "";
        if (lastRole === "user") {
          setSessionStatus(data.session_id, "stale", "reconnect_sync", "stale");
        } else {
          setSessionStatus(data.session_id, "idle", "reconnect_sync", "idle");
        }
        setSending(false);
      }
      break;
    case "compaction":
      insertSystemMsg(`Context compacted — archived ${data.archived} turns, kept ${data.kept}.`);
      break;
    case "done":
      if (data.text && !state._aiMsgEl) {
        appendChunk(data.text);
      }
      debugUiLifecycle("session_done", { session_id: getCurrentSessionId(), tab: state.tab });
      if (getCurrentSessionId()) markSessionIdle(getCurrentSessionId());
      finalizeAiMsg();
      state.inTokens  += data.input_tokens  || 0;
      state.outTokens += data.output_tokens || 0;
      if (data.stop_reason === "max_tokens") {
        insertSystemMsg("⚠ Response was cut off (max_tokens reached). Try increasing max_tokens in Settings → System.");
      } else if (data.stop_reason === "max_tool_rounds") {
        insertSystemMsg(`⚠ Stopped after ${data.rounds_used} tool rounds (limit reached).`);
      }
      updateTokenStats();
      setSending(false);
      send({ type: "list_agents" });
      send({ type: "list_sessions", workspace: state.activeWorkspace || "" });
      break;
    case "error":
      debugUiLifecycle("session_error", { session_id: getCurrentSessionId(), tab: state.tab, message: data.message || "" });
      if (getCurrentSessionId()) markSessionIdle(getCurrentSessionId());
      finalizeAiMsg();
      insertErrorMsg(data.message || "Unknown error");
      resetTranssionPendingUi(data.message || "");
      setSending(false);
      break;
    case "agents":
      populateAgents(data.items || []);
      renderAgentsPanel(data.items || []);
      break;
    case "agent_detail":
      handleAgentDetail(data.agent);
      break;
    case "agent_created":
      populateAgents(data.agents || []);
      renderAgentsPanel(data.agents || []);
      break;
    case "agent_updated":
      populateAgents(data.agents || []);
      renderAgentsPanel(data.agents || []);
      break;
    case "agent_deleted":
      populateAgents(data.agents || []);
      renderAgentsPanel(data.agents || []);
      break;
    case "sessions":
      renderSessions(data.items || []);
      if (state.tab === "chat" && getCurrentSessionId()) rehydrateInProgressUi(getCurrentSessionId());
      break;
    case "memories": {
      const rid = data.request_id;
      // If request_id is set, only render if it matches current generation (deduplication)
      // If request_id is absent/null, always render (for auto-pushed updates from server)
      if (rid != null && Number(rid) !== memoriesListRequestGen) break;
      renderMemories(data.items || []);
      break;
    }
    case "memories_compacted":
      if (data.ok) {
        showToast(
          `Memories compacted: removed ${data.deleted_junk || 0} junk, `
          + `merged ${data.compressed_sources || 0} notes into ${data.compressed_groups || 0} summaries.`,
          "ok"
        );
        sendListMemories(els.memorySearch?.value?.trim() || "", 20, true);
      } else {
        showToast(`Memory compaction failed: ${data.error || "unknown error"}`, "err");
      }
      break;
    case "memory_deleted":
      onMemoryDeleted(data.note_id, data.ok);
      break;
    case "session_deleted":
      onSessionDeleted(data.session_id, data.ok);
      break;
    case "pipeline_step":
      insertSystemMsg(`Pipeline step [${data.agent}]: ${data.output || ""}`);
      break;
    case "pong":
      break;
    case "models":
      handleModelsResponse(data);
      break;
    case "skills":
      handleSkillsList(data);
      break;
    case "skill_install_progress":
      showToast(data.message || "Installing…", "info");
      break;
    case "skill_install_result":
      handleSkillInstallResult(data);
      break;
    case "skill_saved":
      handleSkillSaved(data);
      break;
    case "skill_deleted":
      handleSkillDeleted(data);
      break;
    case "skill_export_ready":
      handleSkillExportReady(data);
      break;
    case "skill_import_result":
      handleSkillImportResult(data);
      break;
    case "test_provider_step":
      handleTestProviderStep(data);
      break;
    case "test_provider_result":
      handleTestProviderResult(data);
      break;
    case "update_status":
      handleUpdateStatus(data);
      break;
    case "update_available":
      handleUpdateAvailable(data);
      break;
    case "update_progress":
      handleUpdateProgress(data);
      break;
    case "update_result":
      handleUpdateResult(data);
      break;
    case "server_shutdown":
      handleServerShutdown(data);
      break;
    case "todos":
      renderTodos(data.items || []);
      break;
    case "todo_created":
      onTodoCreated(data.item);
      break;
    case "todo_updated":
      onTodoUpdated(data.item);
      break;
    case "todo_deleted":
      onTodoDeleted(data.todo_id, data.ok);
      break;
    case "scheduled_tasks":
      renderScheduledTasks(data.tasks || []);
      break;
    case "task_created":
      onTaskCreated(data.task);
      break;
    case "task_toggled":
      onTaskToggled(data.task_id, data.enabled, data.ok);
      break;
    case "task_triggered":
      break;
    case "task_cancelled":
      if (data.ok) {
        send({ type: "list_scheduled_tasks" });
      }
      break;
    case "transsion_code_sent":
      handleTransssionCodeSent(data);
      break;
    case "transsion_authed":
      handleTransssionAuthed(data);
      break;
    case "transsion_quota_result":
      handleTransssionQuotaResult(data);
      break;
  }
}
