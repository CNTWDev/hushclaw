/**
 * websocket.js — WebSocket connection lifecycle and message dispatcher.
 */

import {
  state, wizard, els,
  send, setConnStatus, showToast, updateTokenStats, setSending,
} from "./state.js";

import {
  appendChunk, finalizeAiMsg, insertSystemMsg, insertErrorMsg,
  insertToolBubble, updateToolBubble, renderSessionHistory,
} from "./chat.js";

import {
  handleConfigStatus, handleConfigSaved, openWizard,
  handleModelsResponse, handleTestProviderStep, handleTestProviderResult,
  resetWizardTimers,
} from "./settings.js";

import {
  populateAgents, renderAgentsPanel, handleAgentDetail,
  renderSessions, renderMemories, onMemoryDeleted, onSessionDeleted,
  handleSkillsList, handleSkillRepos, handleSkillInstallResult, handlePublishSkillUrl,
} from "./panels.js";

import {
  renderTodos, onTodoCreated, onTodoUpdated, onTodoDeleted,
  renderScheduledTasks, onTaskCreated, onTaskToggled,
} from "./tasks.js";

// ── WebSocket URL ──────────────────────────────────────────────────────────

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const host  = location.host || "127.0.0.1:8765";
  const params = new URLSearchParams(location.search);
  const key    = params.get("api_key") || "";
  const q      = key ? `?api_key=${encodeURIComponent(key)}` : "";
  return `${proto}//${host}${q}`;
}

// ── Connection ─────────────────────────────────────────────────────────────

export function connect() {
  if (state.ws && state.ws.readyState <= WebSocket.OPEN) return;

  setConnStatus("reconnecting");
  let ws;
  try {
    ws = new WebSocket(wsUrl());
  } catch (err) {
    setConnStatus("disconnected");
    insertErrorMsg(`WebSocket init failed: ${String(err)}`);
    scheduleReconnect();
    return;
  }
  state.ws = ws;

  ws.onopen = () => {
    setConnStatus("connected");
    state._reconnectDelay = 1000;
    els.btnSend.disabled = false;
    document.getElementById("msg-connecting")?.remove();
    send({ type: "list_agents" });
    send({ type: "list_sessions" });

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
    const reason = ev && ev.reason ? ` (${ev.reason})` : "";
    insertSystemMsg(`Disconnected: code ${ev.code}${reason}`);
    scheduleReconnect();
  };

  ws.onerror = () => {
    insertErrorMsg(`WebSocket error to ${wsUrl()}`);
    ws.close();
  };
}

export function scheduleReconnect() {
  if (state._reconnectTimer) return;
  const delay = state._reconnectDelay;
  state._reconnectDelay = Math.min(delay * 2, 30000);
  state._reconnectTimer = setTimeout(() => {
    state._reconnectTimer = null;
    connect();
  }, delay);
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
      break;
    case "config_saved":
      handleConfigSaved(data);
      break;
    case "config_reloaded":
      showToast("Config reloaded from file", "info");
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: "get_config_status" }));
      }
      break;
    case "session":
      state.session_id = data.session_id;
      els.sessionLabel.textContent = `session: ${data.session_id}`;
      break;
    case "chunk":
      if (data.text) appendChunk(data.text);
      break;
    case "tool_call":
      insertToolBubble(data);
      break;
    case "tool_result":
      updateToolBubble(data);
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
      finalizeAiMsg();
      setSending(false);
      break;
    case "session_history":
      renderSessionHistory(data.session_id, data.turns || []);
      break;
    case "compaction":
      insertSystemMsg(`Context compacted — archived ${data.archived} turns, kept ${data.kept}.`);
      break;
    case "done":
      if (data.text && !state._aiMsgEl) {
        appendChunk(data.text);
      }
      finalizeAiMsg();
      state.inTokens  += data.input_tokens  || 0;
      state.outTokens += data.output_tokens || 0;
      updateTokenStats();
      setSending(false);
      send({ type: "list_agents" });
      send({ type: "list_sessions" });
      break;
    case "error":
      finalizeAiMsg();
      insertErrorMsg(data.message || "Unknown error");
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
      break;
    case "memories":
      renderMemories(data.items || []);
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
    case "skill_repos":
      handleSkillRepos(data);
      break;
    case "skill_install_progress":
      showToast(data.message || "Installing…", "info");
      break;
    case "skill_install_result":
      handleSkillInstallResult(data);
      break;
    case "publish_skill_url":
      handlePublishSkillUrl(data);
      break;
    case "test_provider_step":
      handleTestProviderStep(data);
      break;
    case "test_provider_result":
      handleTestProviderResult(data);
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
  }
}
