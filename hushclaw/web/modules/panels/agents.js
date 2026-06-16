/**
 * panels/agents.js — neutral AgentOS runtime agent panel.
 */

import {
  state, els, agentsState, send, sendListMemories, sendListProfileFacts, escHtml, showToast,
  getCurrentSessionId, isSessionRunning, syncComposerState, debugUiLifecycle,
} from "../state.js";
import { rehydrateInProgressUi } from "../chat.js";
import { openConfirm } from "../modal.js";

const LAST_TAB_KEY = "hushclaw.ui.last-tab";
const AGENT_NAME_RE = /^[A-Za-z0-9_.-]+$/;

const _tagsToText = (arr) => Array.isArray(arr) ? arr.join(", ") : "";
const _textToTags = (txt) => (txt || "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

function _runtimeStatusFor(name) {
  return agentsState.runtimeStatusByAgent?.[name] || null;
}

function _runtimeWarningCount(status) {
  return Array.isArray(status?.warnings) ? status.warnings.length : 0;
}

function _agentNeedsAttention(agent, status) {
  const desc = String(agent.description || "").trim();
  const tags = Array.isArray(agent.routing_tags) ? agent.routing_tags : [];
  return !desc || !tags.length || _runtimeWarningCount(status) > 0;
}

function _renderRuntimePills(status) {
  if (!status.ok) {
    return `<span class="agent-status-pill bad">runtime error</span>`;
  }
  const warnings = Array.isArray(status.warnings) ? status.warnings : [];
  const toolMode = status.inherits_global_tools ? "global tools" : "custom tools";
  const skillState = status.can_load_skills ? "skills ready" : "skills blocked";
  return `
    <span class="agent-status-pill ${status.inherits_global_tools ? "neutral" : "info"}">${escHtml(toolMode)}</span>
    <span class="agent-status-pill ${status.can_load_skills ? "ok" : "warn"}">${escHtml(skillState)}</span>
    ${warnings.length ? `<span class="agent-status-pill warn">${warnings.length} warning${warnings.length === 1 ? "" : "s"}</span>` : ""}
  `;
}

function _renderAgentTest(agent) {
  const result = agentsState.testResults?.[agent.name] || null;
  const running = agentsState.runningTestAgent === agent.name;
  const draft = agentsState.testDrafts?.[agent.name] || "";
  return `
    <div class="agent-test-panel">
      <div class="agent-test-row">
        <input class="agent-test-input" data-name="${escHtml(agent.name)}" type="text"
          value="${escHtml(draft)}" placeholder="Test this agent with one prompt" autocomplete="off">
        <button class="btn-agent-test-run" data-name="${escHtml(agent.name)}" ${running ? "disabled" : ""}>
          ${running ? "Running…" : "Run"}
        </button>
      </div>
      ${result ? `
        <div class="agent-test-result ${result.ok ? "ok" : "bad"}">
          ${escHtml(result.ok ? result.text || "(empty response)" : result.error || "Test failed")}
        </div>` : ""}
    </div>`;
}

function _renderAgentEditForm(agent, def) {
  return `
    <div class="agent-edit-form agent-row-edit">
      <label>Description <input id="aedit-desc" type="text" value="${escHtml(def.description || "")}" autocomplete="off"></label>
      <label>Routing tags <input id="aedit-tags" type="text" value="${escHtml(_tagsToText(def.routing_tags))}" autocomplete="off"></label>
      <label>Tools <span class="aedit-hint">comma-separated · blank = inherit global · keep search_skills plus use_skill or skill_view for dynamic skills</span><input id="aedit-tools" type="text" value="${escHtml(_tagsToText(def.tools))}" placeholder="recall, fetch_url, search_skills, use_skill" autocomplete="off"></label>
      <details class="agent-advanced-create">
        <summary>Prompt</summary>
        <label>System Prompt <textarea id="aedit-system" rows="5">${escHtml(def.system_prompt || "")}</textarea></label>
        <label>Instructions <textarea id="aedit-instr" rows="3">${escHtml(def.instructions || "")}</textarea></label>
      </details>
      <div class="agent-edit-actions">
        <button class="btn-aedit-save" data-name="${escHtml(agent.name)}">Save</button>
        <button class="btn-aedit-cancel secondary">Cancel</button>
      </div>
    </div>`;
}

function _visibleAgents() {
  const q = (agentsState.query || "").trim().toLowerCase();
  const filter = agentsState.filter || "all";
  return (agentsState.items || [])
    .filter((agent) => {
      const status = _runtimeStatusFor(agent.name);
      const tags = Array.isArray(agent.routing_tags) ? agent.routing_tags : [];
      const haystack = [agent.name, agent.description, ...tags].join(" ").toLowerCase();
      if (q && !haystack.includes(q)) return false;
      if (filter === "custom" && !agent.editable) return false;
      if (filter === "config" && agent.editable) return false;
      if (filter === "attention" && !_agentNeedsAttention(agent, status)) return false;
      return true;
    })
    .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
}

function _requestRuntimeStatuses(agents) {
  for (const agent of agents || []) {
    if (!_runtimeStatusFor(agent.name)) {
      send({ type: "get_agent_runtime_status", name: agent.name });
    }
  }
}

function _loadTabData(tab) {
  if (tab === "chat") {
    const sid = getCurrentSessionId();
    if (sid && isSessionRunning(sid)) {
      rehydrateInProgressUi(sid);
    }
    syncComposerState();
    return;
  }
  if (tab === "memories") {
    send({ type: "get_memory_overview" });
    sendListMemories("", 50, false, 0, ["user_model", "project_knowledge", "decision"]);
    send({ type: "get_learning_state" });
    send({ type: "list_belief_models" });
    send({ type: "list_opinion_threads", limit: 50 });
    sendListProfileFacts();
    return;
  }
  if (tab === "app-connectors") {
    send({ type: "get_config_status" });
    import("./app_connectors.js").then(({ renderAppConnectorsPanel }) => renderAppConnectorsPanel());
    return;
  }
  if (tab === "agents") {
    send({ type: "list_agents" });
    return;
  }
  if (tab === "skills") {
    send({ type: "list_skills" });
    send({ type: "get_learning_state" });
    return;
  }
  if (tab === "tasks") {
    import("../tasks.js").then(({ refreshTodos, populateSchedAgentSelect }) => {
      refreshTodos(0);
      populateSchedAgentSelect();
    });
    send({ type: "list_scheduled_tasks" });
    return;
  }
  if (tab === "insights") {
    import("../insights.js").then(({ refreshInsights }) => refreshInsights(0));
    return;
  }
  if (tab === "calendar") {
    send({ type: "list_calendar_events" });
    return;
  }
  if (tab === "logs") {
    import("./logs.js").then(({ initLogsPanel, refreshLogs }) => {
      initLogsPanel();
      refreshLogs();
    });
  }
}

export function switchTab(tab) {
  if (tab === "enterprise" && !_isEnterpriseRuntime()) {
    tab = "chat";
  }

  if (tab === "settings") {
    import("../settings.js").then(({ openWizard }) => {
      openWizard(true);
    });
    send({ type: "get_config_status" });
    return;
  }

  const tabBtn = document.querySelector(`.tab[data-tab="${tab}"]`);
  const tabPanel = document.getElementById(`panel-${tab}`);
  const resolvedTab = (tabBtn && tabPanel) ? tab : "chat";
  const isAlreadyActive = state.tab === resolvedTab;

  if (isAlreadyActive) {
    const targetHash = `#tab=${encodeURIComponent(resolvedTab)}`;
    if (location.hash !== targetHash) {
      history.replaceState(null, "", targetHash);
    }
    try { localStorage.setItem(LAST_TAB_KEY, resolvedTab); } catch { /* ignore */ }
    _loadTabData(resolvedTab);
    return;
  }

  state.tab = resolvedTab;
  const targetHash = `#tab=${encodeURIComponent(resolvedTab)}`;
  if (location.hash !== targetHash) {
    history.replaceState(null, "", targetHash);
  }
  try { localStorage.setItem(LAST_TAB_KEY, resolvedTab); } catch { /* ignore */ }
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === resolvedTab);
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `panel-${resolvedTab}`);
  });
  import("../plugin-host.js").then(({ notifyTabActivated }) => notifyTabActivated(resolvedTab));
  debugUiLifecycle("switch_tab", {
    tab: resolvedTab,
    session_id: getCurrentSessionId(),
    sending: state.sending,
  });
  _loadTabData(resolvedTab);
}

export function populateAgents(items) {
  state.agents = items.length ? items : [{ name: "default", description: "" }];

  if (!els.agentSelect) return;
  els.agentSelect.innerHTML = "";
  if (!items.length) {
    const opt = document.createElement("option");
    opt.value = "default"; opt.textContent = "default";
    els.agentSelect.appendChild(opt);
    return;
  }
  items.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.name;
    opt.textContent = a.name + (a.description ? ` — ${a.description}` : "");
    if (a.name === state.agent) opt.selected = true;
    els.agentSelect.appendChild(opt);
  });
}

export function renderAgentsPanel(items) {
  if (items) agentsState.items = items;
  const el = document.getElementById("agents-list");
  if (!el) return;

  if (agentsState.addingNew) {
    el.innerHTML = `
      <div class="agent-edit-form agent-edit-form-standalone">
        <div class="agent-edit-title">New Agent</div>
        <label>Name <input id="anew-name" type="text" placeholder="my-agent" autocomplete="off"></label>
        <label>Description <input id="anew-desc" type="text" placeholder="What does this agent do?" autocomplete="off"></label>
        <label>Routing tags <input id="anew-tags" type="text" placeholder="research, writing" autocomplete="off"></label>
        <details class="agent-advanced-create">
          <summary>Advanced</summary>
          <label>Tools <span class="aedit-hint">comma-separated · blank = inherit global · dynamic skills need search_skills plus use_skill or skill_view</span><input id="anew-tools" type="text" placeholder="recall, fetch_url, search_skills, use_skill" autocomplete="off"></label>
          <label>System Prompt <textarea id="anew-system" rows="4" placeholder="You are..."></textarea></label>
          <label>Instructions <textarea id="anew-instr" rows="3" placeholder="Always reply in..."></textarea></label>
        </details>
        <div class="agent-edit-actions">
          <button id="btn-anew-submit">Create</button>
          <button id="btn-anew-cancel" class="secondary">Cancel</button>
        </div>
      </div>`;
    el.querySelector("#btn-anew-cancel").addEventListener("click", () => {
      agentsState.addingNew = false;
      renderAgentsPanel();
    });
    el.querySelector("#btn-anew-submit").addEventListener("click", () => {
      const name = el.querySelector("#anew-name").value.trim();
      if (!name) {
        showToast("Agent name is required.", "err");
        el.querySelector("#anew-name").focus();
        return;
      }
      if (!AGENT_NAME_RE.test(name)) {
        showToast("Invalid agent name. Use only letters, numbers, '.', '_' or '-'.", "err");
        el.querySelector("#anew-name").focus();
        return;
      }
      send({
        type: "create_agent",
        name,
        description:   el.querySelector("#anew-desc").value.trim(),
        routing_tags:  _textToTags(el.querySelector("#anew-tags").value),
        tools:         _textToTags(el.querySelector("#anew-tools").value),
        system_prompt: el.querySelector("#anew-system").value,
        instructions:  el.querySelector("#anew-instr").value,
      });
      agentsState.addingNew = false;
      agentsState.runtimeStatusByAgent = {};
    });
    return;
  }

  if (!agentsState.items.length) {
    el.innerHTML = '<div class="empty-state">No agents yet.</div>';
    return;
  }

  _requestRuntimeStatuses(agentsState.items);
  const all = agentsState.items || [];
  const customCount = all.filter((a) => a.editable).length;
  const attentionCount = all.filter((a) => _agentNeedsAttention(a, _runtimeStatusFor(a.name))).length;
  const list = _visibleAgents();

  el.innerHTML = `
    <div class="agents-workbench">
      <div class="agents-summary">
        <div><b>${all.length}</b><span>agents</span></div>
        <div><b>${customCount}</b><span>custom</span></div>
        <div><b>${attentionCount}</b><span>need attention</span></div>
        <div><b>${escHtml(state.agent || "default")}</b><span>current</span></div>
      </div>
      <div class="agents-filterbar">
        <input id="agent-search" type="search" placeholder="Search agents" value="${escHtml(agentsState.query || "")}" autocomplete="off">
        <div class="agents-filter-tabs">
          ${["all", "custom", "config", "attention"].map((f) => `
            <button type="button" class="agent-filter-btn${(agentsState.filter || "all") === f ? " active" : ""}" data-filter="${f}">
              ${f === "attention" ? "Needs attention" : f[0].toUpperCase() + f.slice(1)}
            </button>`).join("")}
        </div>
      </div>
      <div class="agents-table"></div>
    </div>`;

  el.querySelector("#agent-search")?.addEventListener("input", (ev) => {
    agentsState.query = ev.target.value || "";
    renderAgentsPanel();
  });
  el.querySelectorAll(".agent-filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      agentsState.filter = btn.dataset.filter || "all";
      renderAgentsPanel();
    });
  });

  const table = el.querySelector(".agents-table");
  if (!table) return;
  if (!list.length) {
    table.innerHTML = '<div class="empty-state">No matching agents.</div>';
    return;
  }

  list.forEach((a) => {
    const status = _runtimeStatusFor(a.name);
    const sourceBadge = a.editable
      ? '<span class="agent-source-pill custom">custom</span>'
      : '<span class="agent-source-pill config">config</span>';
    const desc = (a.description || "").trim() || "No description.";
    const tags = Array.isArray(a.routing_tags) ? a.routing_tags : [];
    const isCurrent = state.agent === a.name;
    const warningBits = [];
    if (!(a.description || "").trim()) warningBits.push("missing description");
    if (!tags.length) warningBits.push("manual routing only");
    if (status?.warnings?.length) warningBits.push(...status.warnings);
    const warningMarkup = warningBits.length
      ? `<div class="agent-row-warnings">${warningBits.map((w) => `<span>${escHtml(w)}</span>`).join("")}</div>`
      : "";
    const editMarkup = agentsState.editingAgent === a.name && agentsState.agentDetail?.name === a.name
      ? _renderAgentEditForm(a, agentsState.agentDetail)
      : "";
    const testOpen = agentsState.testingAgent === a.name;
    const extraMarkup = editMarkup || (testOpen ? _renderAgentTest(a) : "");

    const row = document.createElement("div");
    row.className = "agent-list-row";
    row.dataset.agentRow = a.name;
    row.innerHTML = `
      <div class="agent-row-main">
        <div class="agent-row-head">
          <div class="agent-row-title">
            <span class="agent-row-name">${escHtml(a.name)}</span>
            ${sourceBadge}
            ${isCurrent ? '<span class="agent-source-pill current">current</span>' : ""}
          </div>
          <div class="agent-row-actions">
            <button type="button" class="btn-agent-test" data-name="${escHtml(a.name)}">Test</button>
            <button type="button" class="btn-agent-current secondary" data-name="${escHtml(a.name)}" ${isCurrent ? "disabled" : ""}>Set current</button>
            <button type="button" class="btn-aedit-open secondary" data-name="${escHtml(a.name)}" ${a.editable ? "" : "disabled"}>Edit</button>
            <button type="button" class="btn-adelete danger" data-name="${escHtml(a.name)}" ${a.editable ? "" : "disabled"}>Delete</button>
          </div>
        </div>
        <div class="agent-row-desc">${escHtml(desc)}</div>
        <div class="agent-row-meta">
          <div class="agent-tag-row">${tags.length ? tags.map((tag) => `<span class="cap-tag">${escHtml(tag)}</span>`).join("") : '<span class="agent-muted">manual routing only</span>'}</div>
          <div class="agent-status-row">${status ? _renderRuntimePills(status) : '<span class="agent-status-pill neutral">checking</span>'}</div>
        </div>
        ${warningMarkup}
        ${extraMarkup}
      </div>`;

    row.querySelector(".btn-agent-test")?.addEventListener("click", () => {
      agentsState.testingAgent = agentsState.testingAgent === a.name ? null : a.name;
      agentsState.editingAgent = null;
      renderAgentsPanel();
    });
    row.querySelector(".btn-agent-current")?.addEventListener("click", () => {
      state.agent = a.name;
      if (els.agentSelect) els.agentSelect.value = a.name;
      showToast(`Current agent: ${a.name}`, "ok");
      renderAgentsPanel();
    });
    row.querySelector(".btn-aedit-open")?.addEventListener("click", () => {
      if (!a.editable) return;
      agentsState.editingAgent = a.name;
      agentsState.testingAgent = null;
      agentsState.agentDetail = null;
      send({ type: "get_agent", name: a.name });
      renderAgentsPanel();
    });
    row.querySelector(".btn-adelete")?.addEventListener("click", async () => {
      if (!a.editable) return;
      const confirmed = await openConfirm({
        title: "Delete agent",
        message: `Delete agent "${a.name}"? This cannot be undone.`,
        confirmText: "Delete",
        cancelText: "Cancel",
        dangerConfirm: true,
      });
      if (!confirmed) return;
      delete agentsState.runtimeStatusByAgent[a.name];
      send({ type: "delete_agent", name: a.name });
    });
    row.querySelector(".btn-open-skills")?.addEventListener("click", () => switchTab("skills"));
    row.querySelector(".btn-aedit-cancel")?.addEventListener("click", () => {
      agentsState.editingAgent = null;
      agentsState.agentDetail = null;
      renderAgentsPanel();
    });
    row.querySelector(".btn-aedit-save")?.addEventListener("click", () => {
      const box = row.querySelector(".agent-edit-form");
      delete agentsState.runtimeStatusByAgent[a.name];
      send({
        type: "update_agent",
        name: a.name,
        description:   box.querySelector("#aedit-desc")?.value,
        routing_tags:  _textToTags(box.querySelector("#aedit-tags")?.value),
        tools:         _textToTags(box.querySelector("#aedit-tools")?.value),
        system_prompt: box.querySelector("#aedit-system")?.value,
        instructions:  box.querySelector("#aedit-instr")?.value,
      });
      agentsState.editingAgent = null;
      agentsState.agentDetail = null;
    });
    row.querySelector(".agent-test-input")?.addEventListener("input", (ev) => {
      agentsState.testDrafts[a.name] = ev.target.value || "";
    });
    row.querySelector(".agent-test-input")?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") row.querySelector(".btn-agent-test-run")?.click();
    });
    row.querySelector(".btn-agent-test-run")?.addEventListener("click", () => {
      const text = (agentsState.testDrafts[a.name] || "").trim();
      if (!text) {
        showToast("Test prompt is required.", "err");
        row.querySelector(".agent-test-input")?.focus();
        return;
      }
            const requestId = `${a.name}:${Date.now()}`;
      agentsState.testingAgent = a.name;
      agentsState.runningTestAgent = a.name;
      agentsState.testResults[a.name] = null;
      renderAgentsPanel();
      send({ type: "test_agent", agent: a.name, text, request_id: requestId, session_id: `agent-test-${a.name}` });
    });

    table.appendChild(row);
  });
}

export function handleAgentDetail(def) {
  if (!def) return;
  agentsState.agentDetail = def;
  renderAgentsPanel();
}

export function handleAgentRuntimeStatus(data) {
  if (!data) return;
  agentsState.runtimeStatusByAgent[data.agent] = data;
  renderAgentsPanel();
}

export function handleAgentTestResult(data) {
  if (!data?.agent) return;
  agentsState.testingAgent = data.agent;
  if (agentsState.runningTestAgent === data.agent) agentsState.runningTestAgent = null;
  agentsState.testResults[data.agent] = data.ok
    ? { ok: true, text: data.text || "" }
    : { ok: false, error: data.error || "Test failed" };
  renderAgentsPanel();
}
