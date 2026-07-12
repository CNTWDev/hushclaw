/**
 * websocket.js — WebSocket connection lifecycle and message dispatcher.
 */

import {
  state, wizard, els, updateState,
  send, flushPendingSendQueue, sendListMemories, memoriesListRequestGen, setConnStatus, showToast, updateTokenStats, setSending,
  markSessionRunning, setSessionStatus, getSessionStatus,
  setSessionRuntime, noteSessionChildRun, getCurrentSessionId, setCurrentSessionId, syncComposerState, debugUiLifecycle,
  pushSessionRuntimeEvent, pushWorkbenchActivity, replaceSessionRuntimeFeed,
} from "./state.js";

import {
  appendChunk, setChunkText, finalizeAiMsg, finalizeAiMsgNow, discardActiveAiMsg, insertSystemMsg, insertErrorMsg,
  insertToolBubble, updateToolBubble, renderSessionHistory, rehydrateInProgressUi, noteSessionHistoryReceived,
  insertRoundLine, createToolRound, showAiProgress, setActiveRoundLabel,
  applyLiveMessageIds,
} from "./chat.js";
import { refreshComposerAutocomplete } from "./events/autocomplete.js";

import {
  handleConfigStatus, handleConfigSaved, openWizard,
  handleModelsResponse, handleTestProviderStep, handleTestProviderResult,
  handleTestIntegrationStep, handleTestIntegrationResult,
  handleTransssionCodeSent, handleTransssionAuthed, handleTransssionQuotaResult,
  resetTranssionPendingUi,
  resetWizardTimers,
} from "./settings.js";

import {
  populateAgents, renderAgentsPanel, handleAgentDetail, handleAgentRuntimeStatus, handleAgentTestResult,
  renderSessions, renderSessionSearchResults, refreshSessionsView, updateSessionPaging, ensureSessionRowVisible,
  renderMemories, renderBeliefModels, renderBeliefModelsError,
  handleBeliefModelDetail,
  renderOpinionThreads, renderOpinionThreadsError, handleOpinionThreadDetail,
  renderProfileFacts, renderProfileFactsError,
  renderMemoryOverview, renderReflections,
  onMemoryDeleted, onProfileFactDeleted, onSessionDeleted, onSessionRenamed, handleSessionWorkspaceMoved,
  handleSkillsList, handleSkillRepos, handleSkillSourceInspected, handleSkillInstallProgress, handleSkillInstallResult,
  handleSkillSaved, handleSkillDeleted, handleSkillOverridesPruned, handleSkillExportReady, handleSkillImportResult, handleLearningState,
  handleSkillDetail, handleSkillsHealth, handleSkillEnabled,
  renderAppConnectorsPanel, handleTestAppConnectorResult as handlePanelTestAppConnectorResult,
  switchTab, renderWorkspaceSelector,
  updateSessionRunIndicator,
  renderFiles, refreshFilesList, ensureFilesListLoaded, handleFileIngested, handleFileDeleted, noteGeneratedArtifacts,
  renderLogs,
} from "./panels.js";

import {
  renderTodos, onTodoCreated, onTodoUpdated, onTodoDeleted,
  refreshTodos,
  renderWorkTasks, onWorkTaskCreated, refreshWorkTasks,
  renderScheduledTasks, onTaskCreated, onTaskToggled,
} from "./tasks.js";
import {
  renderInsights, onInsightCreated, onInsightDeleted, refreshInsights,
  handleInsightCleanupPreview, handleInsightCleanupApplied,
} from "./insights.js";
import {
  renderCalendarEvents, onCalendarEventCreated, onCalendarEventUpdated, onCalendarEventDeleted,
  onCalendarSyncDone, resetCalSyncUi,
} from "./calendar.js";
import {
  refreshChatStats, setAgentStats, setSessionStats, setSkillStats,
} from "./stats.js";
import {
  handlePrepareUpdateResult,
  handleUpdateStatus, handleUpdateAvailable, handleUpdateProgress, handleUpdateResult,
  handleServerShutdown, refreshUpdateUi, requestCheckUpdate, notifyUpgradeReconnected,
} from "./updates.js";
import { t } from "./i18n.js";

let _activeReplaySessionId = "";
let _sessionListRefreshTimers = new Map();

function scheduleSessionListRefresh(sessionId, delays = [300, 1400]) {
  const sid = String(sessionId || "").trim();
  if (!sid) return;
  const existing = _sessionListRefreshTimers.get(sid) || [];
  existing.forEach((timer) => clearTimeout(timer));
  const timers = delays.map((delay) => setTimeout(() => {
    refreshSessionsView();
  }, delay));
  _sessionListRefreshTimers.set(sid, timers);
}

function refreshAgentsAfterMutation(items = [], createdName = "") {
  const list = Array.isArray(items) ? [...items] : [];
  const name = String(createdName || "").trim();
  if (name && !list.some((agent) => agent?.name === name)) {
    list.push({ name, description: "", routing_tags: [], editable: true });
  }
  if (list.length) {
    populateAgents(list);
    renderAgentsPanel(list);
  }
  send({ type: "list_agents" });
}

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
    statusEl.textContent = t("ws_connecting");
    statusEl.className = "startup-status";
  } else if (n === 1) {
    statusEl.textContent = t("ws_retrying");
    statusEl.className = "startup-status";
  } else {
    statusEl.textContent = t("ws_still_waiting").replace("{n}", n + 1);
    statusEl.className = "startup-status warn";
  }
  if (hintEl) {
    if (n >= 4) {
      hintEl.textContent = t("ws_slow_start");
    } else if (n >= 2) {
      hintEl.textContent = t("ws_starting_soon");
    } else {
      hintEl.textContent = t("ws_starting");
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
  cd.textContent = t("ws_retry_in").replace("{s}", remaining);
  state._reconnectCountdownTimer = setInterval(() => {
    remaining--;
    if (remaining <= 0) {
      cd.textContent = t("ws_connecting_brief");
      clearInterval(state._reconnectCountdownTimer);
      state._reconnectCountdownTimer = null;
    } else {
      cd.textContent = t("ws_retry_in").replace("{s}", remaining);
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
    // During an upgrade the progress modal covers the screen — suppress the
    // reconnect banner so it doesn't appear behind/around the modal.
    if (!updateState.expectingDisconnect) {
      showReconnectBanner();
      updateReconnectMsg(t("ws_reconnecting"));
    }
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
    syncComposerState();
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
      notifyUpgradeReconnected();   // update modal to "Verifying upgrade…"
      insertSystemMsg("Reconnected — verifying upgrade…");
      requestCheckUpdate(true);  // handleUpdateStatus will resolve the outcome
    }

    // On initial connect, restore any pending tab (must be after ws is ready)
    if (state._tabToRestorePending) {
      const tabToRestore = state._tabToRestorePending;
      state._tabToRestorePending = null;
      switchTab(tabToRestore);
    } else {
      switchTab(state.tab || "chat");
    }

    flushPendingSendQueue();
    send({ type: "list_agents" });
    send({ type: "list_skills" });
    refreshSessionsView();
    ensureFilesListLoaded({ sync: true });
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
    state._pendingSessionStart = false;
    resetCalSyncUi();  // re-enable sync buttons immediately if a sync was in-flight
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
    syncComposerState();
    scheduleReconnect();
  };

  // onerror always precedes onclose — let onclose handle all UI updates
  ws.onerror = () => { ws.close(); };
}

export function scheduleReconnect() {
  if (state._reconnectTimer) return;
  const delay = state._reconnectDelay;
  state._reconnectDelay = Math.min(delay * 2, 30000);

  // Drive countdown in the reconnect banner — suppressed during upgrade
  // (the upgrade modal already provides visual feedback).
  if (!state._isInitialConnect && !updateState.expectingDisconnect) {
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
  updateSessionRunIndicator(sid, status === "running");
  debugUiLifecycle("session_status", { session_id: sid, status, reason, tab: state.tab });
  if (sid === getCurrentSessionId()) {
    if (status === "running") {
      rehydrateInProgressUi(sid);
    } else if (status === "idle" || status === "offline" || status === "stale") {
      rehydrateInProgressUi(sid);
      if (status === "offline") {
        insertSystemMsg("Connection lost. Reconnecting…");
      } else if (status === "stale") {
        insertSystemMsg("Reconnected. Syncing session status…");
      }
    }
    syncComposerState();
  }
}

function applySessionRuntime(data) {
  const sid = data.session_id || getCurrentSessionId();
  if (!sid) return;
  const runtime = data.runtime || {};
  const prevStatus = getSessionStatus(sid);
  const status = runtime.status || "idle";
  const running = ["queued", "running"].includes(status);
  const waitingUser = status === "waiting_user";
  setSessionRuntime(sid, runtime);
  if (Array.isArray(runtime.recent_events)) replaceSessionRuntimeFeed(sid, runtime.recent_events);
  updateSessionRunIndicator(sid, running || waitingUser);
  debugUiLifecycle("session_runtime", { session_id: sid, status, phase: runtime.phase || "", tab: state.tab });
  if (sid === getCurrentSessionId()) {
    if (running) {
      rehydrateInProgressUi(sid);
      if (runtime.phase !== "streaming" && runtime.phase !== "tool_call") {
        showAiProgress(runtime.summary || "Thinking…");
      }
    } else if (waitingUser) {
      clearStreamingSessionIfMatches({ session_id: sid });
      state._pendingSessionStart = false;
      finalizeAiMsgNow();
    }
    syncComposerState();
  } else {
    maybeNotifyBackgroundSession(sid, prevStatus, runtime);
  }
}

function maybeNotifyBackgroundSession(sessionId, prevStatus, runtime = {}) {
  const status = runtime.status || "idle";
  if (!status || status === prevStatus) return;
  const shortId = String(sessionId || "").slice(-6);
  const label = shortId ? `Session ${shortId}` : "Session";
  const summary = runtime.summary || "";
  if (status === "waiting_user") {
    pushWorkbenchActivity({
      level: "wait",
      group: "attention",
      title: `${label} needs you`,
      summary: summary || "Waiting for confirmation",
      meta: "Background run",
      actionType: "open_session",
      sessionId,
    });
    showToast(`${label} is waiting for you${summary ? ` · ${summary}` : ""}`, "warn");
  } else if (status === "completed") {
    pushWorkbenchActivity({
      level: "done",
      group: "results",
      title: `${label} completed`,
      summary: summary || "Finished in the background",
      meta: "Background run",
      actionType: "open_session",
      sessionId,
    });
    showToast(`${label} completed`, "info");
  } else if (status === "failed") {
    pushWorkbenchActivity({
      level: "error",
      group: "attention",
      title: `${label} failed`,
      summary: runtime.last_error || summary || "Background run failed",
      meta: "Background run",
      actionType: "open_session",
      sessionId,
    });
    showToast(`${label} failed${runtime.last_error ? ` · ${runtime.last_error}` : ""}`, "error");
  } else if (status === "stopped") {
    pushWorkbenchActivity({
      level: "warn",
      group: "attention",
      title: `${label} stopped`,
      summary: summary || "Stopped before completion",
      meta: "Background run",
      actionType: "open_session",
      sessionId,
    });
    showToast(`${label} stopped`, "warn");
  }
}

function eventSessionId(data) {
  return String(data?.session_id || "").trim();
}

function isCurrentSessionEvent(data) {
  const sid = eventSessionId(data);
  if (!sid) return true;
  return sid === getCurrentSessionId();
}

function clearStreamingSessionIfMatches(data) {
  const sid = eventSessionId(data);
  if (!sid || state._streamingSessionId === sid) state._streamingSessionId = null;
}

function _formatPerfMs(ms) {
  const value = Number(ms || 0);
  if (!Number.isFinite(value) || value <= 0) return "";
  return value >= 1000 ? `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}s` : `${Math.round(value)}ms`;
}

function _perfSummary(perf = {}) {
  if (!perf || typeof perf !== "object") return "";
  const parts = [];
  const firstText = _formatPerfMs(perf.first_visible_chunk_ms || perf.ttft_ms);
  const tools = _formatPerfMs(perf.tool_ms);
  const total = _formatPerfMs(perf.total_ms);
  if (firstText) parts.push(`first text ${firstText}`);
  if (tools) parts.push(`tools ${tools}`);
  if (total) parts.push(`total ${total}`);
  return parts.join(" · ");
}

function markEventSessionRunning(data, mode = "thinking", resetTimer = false) {
  const sid = eventSessionId(data) || getCurrentSessionId();
  if (sid) markSessionRunning(sid, mode, resetTimer);
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
      refreshFilesList();
      break;
    }
    case "files":
      renderFiles(data);
      break;
    case "file_ingested":
      handleFileIngested(data);
      break;
    case "file_deleted":
      handleFileDeleted(data);
      break;
    case "config_status":
      handleConfigStatus(data);
      refreshChatStats();
      renderAppConnectorsPanel();
      // Update workspace selector in sidebar
      renderWorkspaceSelector(data.workspaces || []);
      break;
    case "config_saved":
      handleConfigSaved(data);
      break;
    case "workspace_initialized": {
      const initBtn = document.getElementById("mem-init-workspace-btn");
      const statusEl = document.getElementById("mem-init-ws-status");
      const sysStatusEl = document.getElementById("ws-registry-status");
      if (initBtn) initBtn.disabled = false;
      document.querySelectorAll(".ws-init-btn, #ws-add-create").forEach((el) => { el.disabled = false; });
      if (data.ok) {
        if (statusEl) statusEl.textContent = `✓ Workspace ready at ${data.path}`;
        if (sysStatusEl) sysStatusEl.textContent = `✓ Workspace ready at ${data.path}. Click Save if you also changed the registry list.`;
        // Refresh config status so the UI shows updated badges
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
          state.ws.send(JSON.stringify({ type: "get_config_status" }));
        }
      } else {
        if (statusEl) statusEl.textContent = `✗ ${data.error || "Failed"}`;
        if (sysStatusEl) sysStatusEl.textContent = `✗ ${data.error || "Failed"}`;
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
      ensureSessionRowVisible(data.session_id, { status: "running", summary: "Thinking" });
      scheduleSessionListRefresh(data.session_id);
      setCurrentSessionId(data.session_id);
      state._streamingSessionId = data.session_id;
      markSessionRunning(data.session_id, "thinking", true);
      refreshChatStats();
      syncComposerState();
      break;
    case "session_status":
      applySessionStatus(data);
      break;
    case "session_runtime":
      applySessionRuntime(data);
      break;
    case "chunk":
      if (!isCurrentSessionEvent(data)) break;
      markEventSessionRunning(data, "streaming");
      if (data.text) {
        if (/<\s*[｜|]?\s*DSML\s*[｜|]?\s*(?:tool_calls|invoke)\b/i.test(data.text) || /<\s*invoke\b[^>]*\bname\s*=/i.test(data.text)) {
          console.warn("Dropped textual tool-call markup from chat stream.");
          break;
        }
        appendChunk(data.text, { clientTurnId: data.client_turn_id || "" });
      }
      break;
    case "tool_call":
      if (!isCurrentSessionEvent(data)) break;
      markEventSessionRunning(data, "tooling");
      discardActiveAiMsg();
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: "tool",
        label: data.tool || "tool",
        summary: `Running ${data.tool || "tool"}`,
        ts: Date.now(),
      });
      insertToolBubble(data);
      showAiProgress(`正在${data.tool || "处理"}…`);
      break;
    case "round_info":
      if (!isCurrentSessionEvent(data)) break;
      discardActiveAiMsg();
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: "thinking",
        label: "Round",
        summary: data.max_rounds ? `${data.round}/${data.max_rounds}` : `${data.round || 0}`,
        ts: Date.now(),
      });
      createToolRound(data.round, data.max_rounds || 0);
      markEventSessionRunning(data, "thinking");
      setActiveRoundLabel(data.round, data.max_rounds || 0);
      showAiProgress(data.max_rounds ? `继续处理 · 第 ${data.round}/${data.max_rounds} 轮` : "继续处理…");
      break;
    case "tool_result":
      if (!isCurrentSessionEvent(data)) break;
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: data.is_error ? "error" : "done",
        label: data.tool || "tool",
        summary: data.is_error ? "Failed" : "Completed",
        ts: Date.now(),
      });
      updateToolBubble(data);
      showAiProgress(data.is_error ? "遇到小问题，正在调整…" : "结果已收到，正在整理…");
      if (!data.is_error && Array.isArray(data.artifacts) && data.artifacts.length) {
        noteGeneratedArtifacts(data.artifacts, {
          showToast: _activeReplaySessionId !== (eventSessionId(data) || getCurrentSessionId()),
        });
        refreshFilesList();
      }
      if (data.tool === "remember_skill") {
        send({ type: "list_skills" });
      }
      if (data.tool === "evolve_skill") {
        send({ type: "list_skills" });
        send({ type: "get_learning_state" });
      }
      if (data.tool === "add_todo" || data.tool === "complete_todo") {
        refreshTodos(0);
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
    case "awaiting_user":
      if (!isCurrentSessionEvent(data)) break;
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: "wait",
        label: "Waiting",
        summary: "Waiting for your confirmation",
        ts: Date.now(),
      });
      clearStreamingSessionIfMatches(data);
      state._pendingSessionStart = false;
      finalizeAiMsg();
      syncComposerState();
      break;
    case "stopped":
      if (!isCurrentSessionEvent(data)) break;
      debugUiLifecycle("session_stopped", { session_id: eventSessionId(data) || getCurrentSessionId(), tab: state.tab });
      state._pendingSessionStart = false;
      finalizeAiMsg();
      syncComposerState();
      break;
    case "replay_start": {
      // A running session was found; clear any stale in-progress UI and get ready
      // to receive replayed structural events followed by live stream continuation.
      _activeReplaySessionId = String(data.session_id || "");
      const rSid = data.session_id;
      if (rSid && rSid === getCurrentSessionId()) {
        finalizeAiMsg();
        setSessionStatus(rSid, "running", "reconnected", "thinking");
        syncComposerState();
      }
      break;
    }
    case "replay_end": {
      _activeReplaySessionId = "";
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
      if (data.session_id && data.session_id !== getCurrentSessionId()) {
        debugUiLifecycle("drop_stale_session_history", {
          session_id: data.session_id,
          current: getCurrentSessionId(),
        });
        break;
      }
      noteSessionHistoryReceived(
        data.session_id,
        (data.turns || []).length,
        { summary: !!data.summary, lineageCount: (data.lineage || []).length },
      );
      if (data.session_id && data.runtime) {
        setSessionRuntime(data.session_id, data.runtime);
        replaceSessionRuntimeFeed(data.session_id, data.runtime.recent_events || []);
      }
      renderSessionHistory(
        data.session_id,
        data.turns || [],
        data.summary || "",
        data.lineage || [],
      );
      if (data.session_id === getCurrentSessionId() && getSessionStatus(data.session_id) === "stale") {
        const turns = data.turns || [];
        const lastRole = turns.length ? String(turns[turns.length - 1]?.role || "") : "";
        if (lastRole === "user") {
          setSessionStatus(data.session_id, "stale", "reconnect_sync", "stale");
        } else {
          setSessionStatus(data.session_id, "idle", "reconnect_sync", "idle");
        }
        syncComposerState();
      }
      break;
    case "compaction":
      if (!isCurrentSessionEvent(data)) break;
      if (data.effective === false) break;
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: "info",
        label: "Context compacted",
        summary: `Archived ${Number(data.archived_messages ?? data.archived ?? 0)}, kept ${Number(data.kept_messages ?? data.kept ?? 0)}`,
        ts: Date.now(),
      });
      break;
    case "done":
      if (!isCurrentSessionEvent(data)) {
        scheduleSessionListRefresh(eventSessionId(data), [260]);
        break;
      }
      clearStreamingSessionIfMatches(data);
      state._pendingSessionStart = false;
      if (data.text) setChunkText(data.text, { clientTurnId: data.client_turn_id || "" });
      applyLiveMessageIds({
        userMessageId: data.user_message_id || "",
        assistantMessageId: data.assistant_message_id || "",
        clientTurnId: data.client_turn_id || "",
      });
      debugUiLifecycle("session_done", { session_id: eventSessionId(data) || getCurrentSessionId(), tab: state.tab });
      finalizeAiMsgNow();
      state.inTokens  += data.input_tokens  || 0;
      state.outTokens += data.output_tokens || 0;
      if (data.stop_reason === "max_tokens") {
        insertSystemMsg("⚠ Response was cut off (max_tokens reached). Try increasing max_tokens in Settings → System.");
      } else if (data.stop_reason === "max_tool_rounds") {
        insertSystemMsg(`⚠ Stopped after ${data.rounds_used} tool rounds (limit reached).`);
      }
      const perfSummary = _perfSummary(data.perf);
      if (perfSummary) {
        pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
          level: "done",
          label: "Timing",
          summary: perfSummary,
          ts: Date.now(),
        });
      }
      updateTokenStats();
      syncComposerState();
      scheduleSessionListRefresh(eventSessionId(data) || getCurrentSessionId(), [260]);
      if (state.tab === "calendar") {
        send({ type: "list_calendar_events" });
      }
      break;
    case "message_state_updated":
      if (!data.ok) {
        insertSystemMsg(`Message update failed: ${data.error || "unknown error"}`);
      }
      break;
    case "error":
      if (!isCurrentSessionEvent(data)) {
        showToast(`Background session failed: ${data.message || "Unknown error"}`, "err");
        scheduleSessionListRefresh(eventSessionId(data), [260]);
        break;
      }
      clearStreamingSessionIfMatches(data);
      state._pendingSessionStart = false;
      debugUiLifecycle("session_error", { session_id: eventSessionId(data) || getCurrentSessionId(), tab: state.tab, message: data.message || "" });
      finalizeAiMsg();
      insertErrorMsg(data.message || "Unknown error");
      resetTranssionPendingUi(data.message || "");
      syncComposerState();
      scheduleSessionListRefresh(eventSessionId(data) || getCurrentSessionId(), [260]);
      break;
    case "agents":
      setAgentStats(data.items || []);
      populateAgents(data.items || []);
      renderAgentsPanel(data.items || []);
      refreshComposerAutocomplete();
      break;
    case "agent_detail":
      handleAgentDetail(data.agent);
      break;
    case "agent_test_result":
      handleAgentTestResult(data);
      break;
    case "agent_created":
      refreshAgentsAfterMutation(data.agents || [], data.name || "");
      break;
    case "agent_updated":
      refreshAgentsAfterMutation(data.agents || []);
      break;
    case "agent_deleted":
      refreshAgentsAfterMutation(data.agents || []);
      break;
    case "sessions": {
      const append = Boolean(data.append ?? data.cursor ?? ((data.offset ?? 0) > 0));
      updateSessionPaging({ items: data.items || [], has_more: !!data.has_more, next_cursor: data.next_cursor || "", append });
      setSessionStats(data.items || [], !!data.has_more, append);
      renderSessions(data.items || [], !!data.has_more, append);
      if (state.tab === "chat" && getCurrentSessionId()) rehydrateInProgressUi(getCurrentSessionId());
      break;
    }
    case "session_search_results":
      renderSessionSearchResults(data.items || [], data.query || "");
      if (state.tab === "chat" && getCurrentSessionId()) rehydrateInProgressUi(getCurrentSessionId());
      break;
    case "session_lineage":
      if (Array.isArray(data.items) && data.items.length) {
        showToast(`Loaded ${data.items.length} lineage event(s) for session ${(data.session_id || "").slice(-12)}`, "info");
      } else {
        showToast(`No lineage recorded for session ${(data.session_id || "").slice(-12)}`, "info");
      }
      break;
    case "memories": {
      const rid = data.request_id;
      // If request_id is set, only render if it matches current generation (deduplication)
      // If request_id is absent/null, always render (for auto-pushed updates from server)
      if (rid != null && Number(rid) !== memoriesListRequestGen) break;
      const append = (data.offset ?? 0) > 0;
      renderMemories(data.items || [], data.has_more ?? false, append);
      break;
    }
    case "memories_compacted":
      if (data.ok) {
        showToast(
          `Memories compacted: removed ${data.deleted_junk || 0} junk, `
          + `merged ${data.compressed_sources || 0} notes into ${data.compressed_groups || 0} summaries.`,
          "ok"
        );
        send({ type: "get_memory_overview" });
        sendListMemories(els.memorySearch?.value?.trim() || "", 50, false);
      } else {
        showToast(`Memory compaction failed: ${data.error || "unknown error"}`, "err");
      }
      break;
    case "memory_deleted":
      onMemoryDeleted(data.note_id, data.ok);
      break;
    case "memory_overview":
      renderMemoryOverview(data);
      break;
    case "belief_models":
      if (data.ok === false) {
        renderBeliefModelsError(data.error || "Unknown error");
        showToast(`Belief models failed to load: ${data.error || "unknown error"}`, "err");
        break;
      }
      renderBeliefModels(data.items || []);
      break;
    case "belief_model_detail":
      handleBeliefModelDetail(data);
      break;
    case "opinion_threads":
      if (data.ok === false) {
        renderOpinionThreadsError(data.error || "Unknown error");
        showToast(`Opinion timeline failed to load: ${data.error || "unknown error"}`, "err");
        break;
      }
      renderOpinionThreads(data.items || [], data);
      break;
    case "opinion_thread_detail":
      handleOpinionThreadDetail(data);
      break;
    case "profile_facts":
      if (data.ok === false) {
        renderProfileFactsError(data.error || "Unknown error");
        showToast(`Profile facts failed to load: ${data.error || "unknown error"}`, "err");
        break;
      }
      renderProfileFacts(data.items || [], data);
      break;
    case "profile_fact_deleted":
      onProfileFactDeleted(data.fact_id, data.ok);
      break;
    case "session_deleted":
      onSessionDeleted(data.session_id, data.ok);
      break;
    case "session_renamed":
      onSessionRenamed(data);
      break;
    case "session_workspace_moved":
      handleSessionWorkspaceMoved(data);
      break;
    case "pipeline_step":
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: "pipeline",
        label: data.agent || "pipeline",
        summary: data.output || "Pipeline step",
        ts: Date.now(),
      });
      break;
    case "user_amendment_queued":
      if (isCurrentSessionEvent(data)) {
        pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
          level: "queued",
          label: "Queued update",
          summary: data.queue_size > 1 ? `${data.queue_size} pending` : "1 pending",
          ts: Date.now(),
        });
      }
      break;
    case "user_amendment_applied":
      if (isCurrentSessionEvent(data)) {
        const safePoint = data.safe_point ? ` (${data.safe_point})` : "";
        pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
          level: "amendment",
          label: "Applying update",
          summary: `Replanning${safePoint}`,
          ts: Date.now(),
        });
      }
      break;
    case "user_amendment_queue_limited":
      if (isCurrentSessionEvent(data)) {
        pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
          level: "warn",
          label: "Merged extra updates",
          summary: "Too many pending updates; merged into the latest one",
          ts: Date.now(),
        });
      }
      break;
    case "run_state_changed":
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: data.state === "superseded" ? "queued" : "info",
        label: data.state || "run",
        summary: data.reason || "",
        ts: data.ts || Date.now(),
      });
      debugUiLifecycle("run_state_changed", {
        session_id: data.session_id || "",
        run_id: data.run_id || "",
        state: data.state || "",
        reason: data.reason || "",
        tab: state.tab,
      });
      break;
    case "thread_state_changed":
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: "thread",
        label: "Thread",
        summary: data.state || "active",
        ts: data.ts || Date.now(),
      });
      debugUiLifecycle("thread_state_changed", {
        session_id: data.session_id || "",
        thread_id: data.thread_id || "",
        state: data.state || "",
        tab: state.tab,
      });
      break;
    case "step_state_changed":
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: data.step_type || "step",
        label: data.step_type || "step",
        summary: data.summary || data.state || "",
        ts: data.ts || Date.now(),
      });
      debugUiLifecycle("step_state_changed", {
        session_id: data.session_id || "",
        run_id: data.run_id || "",
        step_id: data.step_id || "",
        step_type: data.step_type || "",
        state: data.state || "",
        tab: state.tab,
      });
      break;
    case "child_run_state_changed":
      noteSessionChildRun(eventSessionId(data) || getCurrentSessionId(), {
        run_id: data.run_id || "",
        thread_id: data.thread_id || "",
        parent_run_id: data.parent_run_id || "",
        agent_name: data.agent || "",
        trigger_type: data.trigger_type || "",
        run_kind: data.run_kind || "child",
        visibility: data.visibility || "background",
        state: data.state || "",
        summary: data.summary || "",
        updated_at: data.ts || Date.now(),
        active_step: {
          step_id: data.step_id || "",
          step_type: data.step_type || "",
          state: data.step_state || data.state || "",
          summary: data.summary || "",
          meta: data.meta && typeof data.meta === "object" ? data.meta : {},
        },
      });
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: data.state === "failed" ? "error" : (data.state === "completed" ? "done" : (data.state === "paused" ? "wait" : "child")),
        label: data.agent || data.run_kind || "child",
        summary: data.summary || data.state || "",
        ts: data.ts || Date.now(),
        scope: "child",
        state: data.state || "",
        child_run_id: data.run_id || "",
        run_id: data.parent_run_id || "",
      });
      debugUiLifecycle("child_run_state_changed", {
        session_id: data.session_id || "",
        run_id: data.run_id || "",
        parent_run_id: data.parent_run_id || "",
        state: data.state || "",
        agent: data.agent || "",
        tab: state.tab,
      });
      break;
    case "research_job_started":
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: "research",
        label: "Research",
        summary: data.max_urls ? `Planning up to ${data.max_urls} sources · ${data.read_mode || "mixed"}` : `Planning · ${data.read_mode || "mixed"}`,
        ts: data.ts || Date.now(),
      });
      break;
    case "research_queries_planned":
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: "research",
        label: "Plan ready",
        summary: `${data.planned_queries || 0} quer${(data.planned_queries || 0) === 1 ? "y" : "ies"}${data.max_urls ? ` · up to ${data.max_urls} URLs` : ""}`,
        ts: data.ts || Date.now(),
      });
      break;
    case "research_search_progress":
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: "research",
        label: "Searching",
        summary: `${data.completed || 0}/${data.total || 0} queries · ${data.results || 0} results`,
        ts: data.ts || Date.now(),
      });
      break;
    case "research_read_progress":
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: "research",
        label: "Reading",
        summary: `${data.completed || 0}/${data.total || 0} URLs · ${data.ok || 0} readable`,
        ts: data.ts || Date.now(),
      });
      break;
    case "research_job_completed": {
      const telemetry = data.telemetry || {};
      const urlsSelected = telemetry.urls_selected || 0;
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: "done",
        label: "Research",
        summary: urlsSelected ? `Completed with ${urlsSelected} source${urlsSelected === 1 ? "" : "s"}` : (data.summary || "Completed"),
        ts: data.ts || Date.now(),
      });
      break;
    }
    case "research_job_failed":
      pushSessionRuntimeEvent(eventSessionId(data) || getCurrentSessionId(), {
        level: "error",
        label: "Research",
        summary: data.error || "Failed",
        ts: data.ts || Date.now(),
      });
      break;
    case "pong":
      break;
    case "models":
      handleModelsResponse(data);
      break;
    case "skills":
      setSkillStats(data);
      handleSkillsList(data);
      refreshComposerAutocomplete();
      break;
    case "learning_state":
      handleLearningState(data);
      renderReflections(data.reflections, data.skill_outcomes);
      break;
    case "skill_install_progress":
      handleSkillInstallProgress(data);
      break;
    case "skill_source_inspected":
      handleSkillSourceInspected(data);
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
    case "skill_overrides_pruned":
      handleSkillOverridesPruned(data);
      break;
    case "skill_detail":
      handleSkillDetail(data);
      break;
    case "agent_runtime_status":
      handleAgentRuntimeStatus(data);
      break;
    case "skills_health":
      handleSkillsHealth(data);
      break;
    case "skill_enabled":
      handleSkillEnabled(data);
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
    case "test_integration_step":
      handleTestIntegrationStep(data);
      break;
    case "test_integration_result":
      handleTestIntegrationResult(data);
      break;
    case "test_app_connector_result":
      handlePanelTestAppConnectorResult(data);
      break;
    case "update_status":
      handleUpdateStatus(data);
      break;
    case "update_available":
      handleUpdateAvailable(data);
      break;
    case "prepare_update_result":
      handlePrepareUpdateResult(data);
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
      renderTodos(data.items || [], data.has_more, data.offset || 0);
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
    case "insights":
      renderInsights(data.items || [], data.has_more, data.offset || 0, data.view || "curated");
      break;
    case "insight_created":
      onInsightCreated(data.item);
      break;
    case "insight_deleted":
      onInsightDeleted(data.note_id, data.ok);
      break;
    case "insight_cleanup_preview":
      handleInsightCleanupPreview(data);
      break;
    case "insight_cleanup_applied":
      handleInsightCleanupApplied(data);
      break;
    case "scheduled_tasks":
      renderScheduledTasks(data.tasks || []);
      break;
    case "work_tasks":
      renderWorkTasks(data.tasks || []);
      break;
    case "work_task_created":
      onWorkTaskCreated(data.task);
      break;
    case "work_task_claimed":
    case "work_task_completed":
    case "work_task_triggered":
    case "work_task_started":
    case "work_task_run_result":
    case "work_task_retried":
      refreshWorkTasks();
      break;
    case "logs":
      if (data.ok === false) {
        showToast(`Logs failed to load: ${data.error || "unknown error"}`, "err");
      }
      renderLogs(data.items || []);
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
    case "calendar_events":
      renderCalendarEvents(data.items || []);
      break;
    case "calendar_event_created":
      onCalendarEventCreated(data.item);
      break;
    case "calendar_event_updated":
      onCalendarEventUpdated(data.item);
      break;
    case "calendar_event_deleted":
      onCalendarEventDeleted(data.event_id);
      break;
    case "calendar_sync_done":
      onCalendarSyncDone(data);
      break;
  }
}
