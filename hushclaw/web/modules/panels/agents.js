/**
 * panels/agents.js — neutral AgentOS runtime agent panel.
 */

import {
  state, els, agentsState, send, sendListMemories, escHtml, showToast,
  getCurrentSessionId, isSessionRunning, setSending, debugUiLifecycle,
} from "../state.js";
import { rehydrateInProgressUi } from "../chat.js";
import { openConfirm } from "../modal.js";
import { renderLoadingMarkup } from "../loading.js";

const LAST_TAB_KEY = "hushclaw.ui.last-tab";
const AGENT_NAME_RE = /^[A-Za-z0-9_.-]+$/;

const _tagsToText = (arr) => Array.isArray(arr) ? arr.join(", ") : "";
const _textToTags = (txt) => (txt || "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

function _renderAgentSkillStatus(status) {
  if (!status) {
    return `
      <div class="agent-skills">
        <div class="agent-skills-head">
          <span class="agent-detail-field-label">Skills</span>
        </div>
        ${renderLoadingMarkup({ status: "Checking skills…", compact: true, height: 54 })}
      </div>`;
  }
  if (!status.ok) {
    return `
      <div class="agent-skills">
        <div class="agent-skills-head">
          <span class="agent-detail-field-label">Skills</span>
          <span class="agent-health-badge bad">error</span>
        </div>
        <div class="agent-skills-empty">${escHtml(status.error || "Skill status unavailable.")}</div>
      </div>`;
  }

  const summary = status.summary || {};
  const items = Array.isArray(status.items) ? status.items : [];
  const visible = items
    .filter((item) => item.usable || item.blocked_by_tool || item.available === false || item.enabled === false || item.has_conflict)
    .slice(0, 8);
  const hidden = Math.max(0, items.length - visible.length);
  const badgeClass = summary.issues ? "warn" : "ok";
  const accessHint = summary.can_use_prompt_skills
    ? ""
    : `<div class="agent-skills-note">Prompt skills require <span class="inline-code">use_skill</span> or <span class="inline-code">skill_view</span> in this agent's effective tools.</div>`;
  const rows = visible.map((item) => {
    const problems = Array.isArray(item.problems) ? item.problems : [];
    const state = item.usable ? "ok" : item.blocked_by_tool ? "blocked" : "bad";
    const label = item.usable ? "usable" : item.blocked_by_tool ? "tool gated" : "issue";
    const detail = problems.length ? problems[0] : (item.reason || item.description || "");
    const kind = item.direct_tool ? `tool: ${item.direct_tool}` : "prompt";
    return `
      <div class="agent-skill-row ${state}">
        <span class="agent-skill-dot"></span>
        <div class="agent-skill-main">
          <div class="agent-skill-title">
            <span>${escHtml(item.name || "")}</span>
            <span class="agent-skill-scope">${escHtml(item.scope_label || item.scope || "Unknown")}</span>
          </div>
          <div class="agent-skill-desc">${escHtml(detail || item.description || "No description.")}</div>
        </div>
        <div class="agent-skill-side">
          <span class="agent-skill-kind">${escHtml(kind)}</span>
          <span class="agent-skill-state">${escHtml(label)}</span>
        </div>
      </div>`;
  }).join("");

  return `
    <div class="agent-skills">
      <div class="agent-skills-head">
        <span class="agent-detail-field-label">Skills</span>
        <span class="agent-health-badge ${badgeClass}">${summary.issues || 0} issue${summary.issues === 1 ? "" : "s"}</span>
      </div>
      <div class="agent-skill-metrics">
        <span><b>${summary.usable || 0}</b> usable</span>
        <span><b>${summary.blocked || 0}</b> tool gated</span>
        <span><b>${summary.unavailable || 0}</b> unavailable</span>
      </div>
      ${accessHint}
      <div class="agent-skill-list">
        ${rows || '<div class="agent-skills-empty">No skills installed.</div>'}
        ${hidden ? `<div class="agent-skills-more">+ ${hidden} more skills available from the library</div>` : ""}
      </div>
    </div>`;
}

function _fillDetailSlot(cardEl, a, def) {
  const slot = cardEl.querySelector(".agent-detail-slot");
  if (!slot) return;

  if (!def) {
    slot.innerHTML = renderLoadingMarkup({ status: "Loading…", compact: true, height: 72 });
    return;
  }

  const container = document.createElement("div");

  if (agentsState.editingAgent === a.name) {
    container.className = "agent-edit-form";
    container.innerHTML = `
      <label>Description <input id="aedit-desc" type="text" value="${escHtml(def.description || "")}" autocomplete="off"></label>
      <label>Routing tags <input id="aedit-tags" type="text" value="${escHtml(_tagsToText(def.routing_tags))}" autocomplete="off"></label>
      <label>Tools <span class="aedit-hint">comma-separated · blank = inherit global · limiting tools can block skills</span><input id="aedit-tools" type="text" value="${escHtml(_tagsToText(def.tools))}" placeholder="recall, fetch_url, search_notes" autocomplete="off"></label>
      <label>System Prompt <textarea id="aedit-system" rows="5">${escHtml(def.system_prompt || "")}</textarea></label>
      <label>Instructions <textarea id="aedit-instr" rows="3">${escHtml(def.instructions || "")}</textarea></label>
      <div class="agent-edit-actions">
        <button class="btn-aedit-save" data-name="${escHtml(a.name)}">Save</button>
        <button class="btn-aedit-cancel secondary">Cancel</button>
      </div>`;

    container.querySelector(".btn-aedit-save").addEventListener("click", () => {
      send({
        type: "update_agent",
        name: a.name,
        description:   container.querySelector("#aedit-desc")?.value,
        routing_tags:  _textToTags(container.querySelector("#aedit-tags")?.value),
        tools:         _textToTags(container.querySelector("#aedit-tools")?.value),
        system_prompt: container.querySelector("#aedit-system")?.value,
        instructions:  container.querySelector("#aedit-instr")?.value,
      });
      agentsState.editingAgent  = null;
      agentsState.expandedAgent = null;
      agentsState.agentDetail   = null;
      agentsState.agentSkillStatus = null;
      renderAgentsPanel();
    });
    container.querySelector(".btn-aedit-cancel").addEventListener("click", () => {
      agentsState.editingAgent = null;
      _fillDetailSlot(cardEl, a, def);
    });
  } else {
    const sysPrev   = def.system_prompt ? escHtml(def.system_prompt) : '<em>—</em>';
    const instrPrev = def.instructions  ? escHtml(def.instructions)  : '<em>—</em>';
    const modelLine = def.model         ? escHtml(def.model)         : '<em>inherited</em>';
    const tagsLine = (def.routing_tags && def.routing_tags.length)
      ? def.routing_tags.map((t) => `<span class="cap-tag">${escHtml(t)}</span>`).join(" ")
      : '<em>—</em>';
    const toolsLine = (def.tools && def.tools.length)
      ? def.tools.map((t) => `<span class="cap-tag">${escHtml(t)}</span>`).join(" ")
      : '<em>inherited</em>';
    const editBtn = def.editable ? `<button class="btn-aedit-open secondary" data-name="${escHtml(a.name)}">Edit</button>` : "";
    const delBtn  = def.editable ? `<button class="btn-adelete danger" data-name="${escHtml(a.name)}">Delete</button>` : "";
    const skillStatus = agentsState.agentSkillStatus?.agent === a.name ? agentsState.agentSkillStatus : null;

    container.className = "agent-detail";
    container.innerHTML = `
      <div class="agent-detail-grid">
        <span class="agent-detail-key">Routing tags</span><span class="agent-detail-val">${tagsLine}</span>
        <span class="agent-detail-key">Tools</span><span class="agent-detail-val">${toolsLine}</span>
        <span class="agent-detail-key">Model</span><span class="agent-detail-val">${modelLine}</span>
      </div>
      <div class="agent-detail-field">
        <span class="agent-detail-field-label">System Prompt</span>
        <pre class="agent-detail-pre">${sysPrev}</pre>
      </div>
      <div class="agent-detail-field">
        <span class="agent-detail-field-label">Instructions</span>
        <pre class="agent-detail-pre">${instrPrev}</pre>
      </div>
      ${_renderAgentSkillStatus(skillStatus)}
      <div class="agent-edit-actions">${editBtn}${delBtn}</div>`;

    container.querySelector(".btn-aedit-open")?.addEventListener("click", () => {
      agentsState.editingAgent = a.name;
      _fillDetailSlot(cardEl, a, def);
    });
    container.querySelector(".btn-adelete")?.addEventListener("click", async () => {
      const confirmed = await openConfirm({
        title: "Delete agent",
        message: `Delete agent "${a.name}"? This cannot be undone.`,
        confirmText: "Delete",
        cancelText: "Cancel",
        dangerConfirm: true,
      });
      if (!confirmed) return;
      send({ type: "delete_agent", name: a.name });
    });
  }

  slot.innerHTML = "";
  slot.appendChild(container);
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
    if (resolvedTab === "memories") {
      send({ type: "get_memory_overview" });
      sendListMemories("", 50, false, 0, ["user_model", "project_knowledge", "decision"]);
      send({ type: "get_learning_state" });
      send({ type: "list_belief_models" });
      send({ type: "list_profile_facts" });
    }
    if (resolvedTab === "app-connectors") {
      send({ type: "get_config_status" });
      import("./app_connectors.js").then(({ renderAppConnectorsPanel }) => renderAppConnectorsPanel());
    }
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
  if (resolvedTab === "chat") {
    const sid = getCurrentSessionId();
    if (sid && isSessionRunning(sid)) {
      setSending(true);
      rehydrateInProgressUi(sid);
    }
  }
  if (resolvedTab === "memories") {
    send({ type: "get_memory_overview" });
    sendListMemories("", 50, false, 0, ["user_model", "project_knowledge", "decision"]);
    send({ type: "get_learning_state" });
    send({ type: "list_belief_models" });
    send({ type: "list_profile_facts" });
  }
  if (resolvedTab === "app-connectors") {
    send({ type: "get_config_status" });
    import("./app_connectors.js").then(({ renderAppConnectorsPanel }) => renderAppConnectorsPanel());
  }
  if (resolvedTab === "agents") send({ type: "list_agents" });
  if (resolvedTab === "skills") {
    send({ type: "list_skills" });
    send({ type: "get_learning_state" });
  }
  if (resolvedTab === "tasks") {
    send({ type: "list_todos" });
    send({ type: "list_scheduled_tasks" });
    import("../tasks.js").then(({ populateSchedAgentSelect }) => populateSchedAgentSelect());
  }
  if (resolvedTab === "calendar") {
    send({ type: "list_calendar_events" });
  }
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
        <label>Tools <span class="aedit-hint">comma-separated · blank = inherit global</span><input id="anew-tools" type="text" placeholder="recall, fetch_url" autocomplete="off"></label>
        <label>System Prompt <textarea id="anew-system" rows="4" placeholder="You are..."></textarea></label>
        <label>Instructions <textarea id="anew-instr" rows="3" placeholder="Always reply in..."></textarea></label>
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
    });
    return;
  }

  if (!agentsState.items.length) {
    el.innerHTML = '<div class="empty-state">No agents yet.</div>';
    return;
  }

  el.innerHTML = "";
  const list = (agentsState.items || []).slice().sort((a, b) =>
    (a.name || "").localeCompare(b.name || "")
  );
  const grid = document.createElement("div");
  grid.className = "agent-org-chart agent-runtime-grid";

  list.forEach((a) => {
    const isExpanded = agentsState.expandedAgent === a.name;
    const editBadge = a.editable ? "" : ' <span class="agent-badge">config</span>';
    const desc = (a.description || "").trim() || "No description.";
    const tags = Array.isArray(a.routing_tags) ? a.routing_tags : [];
    const avatarHue = [...(a.name || "A")].reduce((acc, c) => acc + c.charCodeAt(0), 0) % 360;
    const avatarLetter = (a.name || "?")[0].toUpperCase();

    const card = document.createElement("div");
    card.className = "list-item agent-item org-card";
    card.dataset.nodeCard = a.name;
    card.innerHTML = `
      <div class="agent-card-main">
        <div class="agent-item-header">
          <div class="agent-avatar" style="--avatar-hue:${avatarHue}">${escHtml(avatarLetter)}</div>
          <div class="agent-meta">
            <div class="agent-name-row">
              <span class="agent-item-name" title="${escHtml(a.name)}">${escHtml(a.name)}${editBadge}</span>
            </div>
            <span class="agent-item-desc" title="${escHtml(desc)}">${escHtml(desc)}</span>
            <div class="agent-tag-row">${tags.map((tag) => `<span class="cap-tag">${escHtml(tag)}</span>`).join("")}</div>
          </div>
          <button type="button" class="btn-agent-toggle${isExpanded ? " is-open" : ""}" data-name="${escHtml(a.name)}"
            title="${isExpanded ? "Collapse" : "Expand"} details" aria-label="${isExpanded ? "Collapse" : "Expand"}"></button>
        </div>
        <div class="agent-detail-slot"></div>
      </div>`;

    if (isExpanded) {
      _fillDetailSlot(card, a, agentsState.agentDetail);
    }

    card.querySelector(".btn-agent-toggle").addEventListener("click", () => {
      if (agentsState.expandedAgent === a.name) {
        agentsState.expandedAgent = null;
        agentsState.agentDetail = null;
        agentsState.agentSkillStatus = null;
        agentsState.editingAgent = null;
        card.querySelector(".agent-detail-slot").innerHTML = "";
        card.querySelector(".btn-agent-toggle").classList.remove("is-open");
      } else {
        if (agentsState.expandedAgent) {
          const prev = document.querySelector(
            `[data-node-card="${CSS.escape(agentsState.expandedAgent)}"]`
          );
          if (prev) {
            prev.querySelector(".agent-detail-slot").innerHTML = "";
            prev.querySelector(".btn-agent-toggle").classList.remove("is-open");
          }
        }
        agentsState.expandedAgent = a.name;
        agentsState.agentDetail = null;
        agentsState.agentSkillStatus = null;
        agentsState.editingAgent = null;
        card.querySelector(".btn-agent-toggle").classList.add("is-open");
        _fillDetailSlot(card, a, null);
        send({ type: "get_agent", name: a.name });
        send({ type: "get_agent_skill_status", name: a.name });
        requestAnimationFrame(() =>
          card.scrollIntoView({ behavior: "smooth", block: "nearest" })
        );
      }
    });

    grid.appendChild(card);
  });

  el.appendChild(grid);
}

export function handleAgentDetail(def) {
  if (!def) return;
  agentsState.agentDetail = def;
  const name = agentsState.expandedAgent;
  if (!name) return;
  const cardEl = document.querySelector(`[data-node-card="${CSS.escape(name)}"]`);
  const agentObj = (agentsState.items || []).find((x) => x.name === name);
  if (cardEl && agentObj) {
    _fillDetailSlot(cardEl, agentObj, def);
  } else {
    renderAgentsPanel();
  }
}

export function handleAgentSkillStatus(data) {
  if (!data) return;
  agentsState.agentSkillStatus = data;
  const name = agentsState.expandedAgent;
  if (!name || data.agent !== name) return;
  const cardEl = document.querySelector(`[data-node-card="${CSS.escape(name)}"]`);
  const agentObj = (agentsState.items || []).find((x) => x.name === name);
  if (cardEl && agentObj) {
    _fillDetailSlot(cardEl, agentObj, agentsState.agentDetail);
  }
}
