/**
 * panels/agents.js — Agent org-chart panel, tab switching.
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

// ── Agent detail slot (in-place expand/collapse, no full re-render) ──────────

/**
 * Fill or update a single card's `.agent-detail-slot` in-place.
 * Works from any callsite — no full renderAgentsPanel() required.
 * @param {HTMLElement} cardEl  The .org-card element.
 * @param {object}      a       Lightweight agent object from agentsState.items.
 * @param {object|null} def     Full agent definition from server, or null → loading.
 */
function _fillDetailSlot(cardEl, a, def) {
  const slot = cardEl.querySelector(".agent-detail-slot");
  if (!slot) return;

  if (!def) {
    slot.innerHTML = renderLoadingMarkup({ status: "Loading…", compact: true, height: 72 });
    return;
  }

  const _capsToText = (arr) => Array.isArray(arr) ? arr.join(", ") : "";
  const _textToCaps = (txt) => (txt || "").split(",").map((s) => s.trim()).filter(Boolean);
  const allNames  = (agentsState.items || []).map((x) => x.name);
  const reportOpts = [
    '<option value="">(none)</option>',
    ...allNames.filter((n) => n !== a.name)
      .map((n) => `<option value="${escHtml(n)}" ${(def.reports_to === n) ? "selected" : ""}>${escHtml(n)}</option>`),
  ].join("");

  const container = document.createElement("div");

  if (agentsState.editingAgent === a.name) {
    // ── Edit form ──────────────────────────────────────────────────────────
    container.className = "agent-edit-form";
    container.innerHTML = `
      <label>Description <input id="aedit-desc" type="text" value="${escHtml(def.description || "")}" autocomplete="off"></label>
      <label>Role
        <select id="aedit-role">
          <option value="specialist" ${((def.role || "specialist") === "specialist") ? "selected" : ""}>specialist</option>
          <option value="commander"  ${((def.role || "specialist") === "commander")  ? "selected" : ""}>commander</option>
        </select>
      </label>
      <div class="agent-governance-header">Governance metadata — controls org structure, not automatic routing</div>
      <label>Team <input id="aedit-team" type="text" value="${escHtml(def.team || "")}" autocomplete="off"></label>
      <label>Reports To
        <select id="aedit-reports-to">
          <option value="">(none)</option>
          ${allNames.filter((n) => n !== a.name).map((n) => `<option value="${escHtml(n)}" ${(def.reports_to === n) ? "selected" : ""}>${escHtml(n)}</option>`).join("")}
        </select>
      </label>
      <label>Capabilities <input id="aedit-caps" type="text" value="${escHtml(_capsToText(def.capabilities))}" autocomplete="off"></label>
      <label>Tools <span class="aedit-hint">comma-separated · blank = inherit global</span><input id="aedit-tools" type="text" value="${escHtml(_capsToText(def.tools))}" placeholder="recall, fetch_url, search_notes" autocomplete="off"></label>
      <label>Model <input id="aedit-model" type="text" value="${escHtml(def.model || "")}" autocomplete="off"></label>
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
        role:          container.querySelector("#aedit-role")?.value,
        team:          container.querySelector("#aedit-team")?.value,
        reports_to:    container.querySelector("#aedit-reports-to")?.value,
        capabilities:  _textToCaps(container.querySelector("#aedit-caps")?.value),
        tools:         _textToCaps(container.querySelector("#aedit-tools")?.value),
        model:         container.querySelector("#aedit-model")?.value,
        system_prompt: container.querySelector("#aedit-system")?.value,
        instructions:  container.querySelector("#aedit-instr")?.value,
      });
      agentsState.editingAgent  = null;
      agentsState.expandedAgent = null;
      agentsState.agentDetail   = null;
      // Full re-render needed because tree structure may change (role/reports_to).
      renderAgentsPanel();
    });
    container.querySelector(".btn-aedit-cancel").addEventListener("click", () => {
      agentsState.editingAgent = null;
      _fillDetailSlot(cardEl, a, def);
    });

  } else {
    // ── View mode ──────────────────────────────────────────────────────────
    const sysPrev   = def.system_prompt ? escHtml(def.system_prompt) : '<em>—</em>';
    const instrPrev = def.instructions  ? escHtml(def.instructions)  : '<em>—</em>';
    const modelLine = def.model         ? escHtml(def.model)          : '<em>inherited</em>';
    const roleLine  = escHtml(def.role || "specialist");
    const teamLine  = def.team         ? escHtml(def.team)           : '<em>—</em>';
    const reportsLine = def.reports_to ? escHtml(def.reports_to)     : '<em>—</em>';
    const capsLine  = (def.capabilities && def.capabilities.length)
      ? def.capabilities.map((c) => `<span class="cap-tag">${escHtml(c)}</span>`).join(" ")
      : '<em>—</em>';
    const toolsLine = (def.tools && def.tools.length)
      ? def.tools.map((t) => `<span class="cap-tag">${escHtml(t)}</span>`).join(" ")
      : '<em>inherited</em>';
    const editBtn   = def.editable ? `<button class="btn-aedit-open secondary" data-name="${escHtml(a.name)}">Edit</button>` : "";
    const delBtn    = def.editable ? `<button class="btn-adelete danger"       data-name="${escHtml(a.name)}">Delete</button>` : "";

    const isQuickEditing = agentsState.quickReportAgent === a.name;
    const reportAdjust = def.editable
      ? (isQuickEditing
        ? `<div class="agent-report-adjust-inline">
            <span class="agent-quick-report-label">Reports to</span>
            <select class="agent-report-select">${reportOpts}</select>
            <button class="secondary btn-agent-report-save" data-name="${escHtml(a.name)}">Apply</button>
            <button class="secondary btn-agent-report-cancel">Cancel</button>
           </div>`
        : `<button class="secondary btn-agent-report-open" data-name="${escHtml(a.name)}">Adjust Reporting</button>`)
      : "";

    container.className = "agent-detail";
    container.innerHTML = `
      <div class="agent-detail-grid">
        <span class="agent-detail-key">Role</span><span class="agent-detail-val">${roleLine}</span>
        <span class="agent-detail-key">Team</span><span class="agent-detail-val">${teamLine}</span>
        <span class="agent-detail-key">Reports to</span><span class="agent-detail-val">${reportsLine}</span>
        <span class="agent-detail-key">Capabilities</span><span class="agent-detail-val">${capsLine}</span>
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
      <div class="agent-edit-actions">${editBtn}${reportAdjust}${delBtn}</div>`;

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
    container.querySelector(".btn-agent-report-open")?.addEventListener("click", () => {
      agentsState.quickReportAgent = a.name;
      _fillDetailSlot(cardEl, a, def);
    });
    container.querySelector(".btn-agent-report-cancel")?.addEventListener("click", () => {
      agentsState.quickReportAgent = null;
      _fillDetailSlot(cardEl, a, def);
    });
    container.querySelector(".btn-agent-report-save")?.addEventListener("click", () => {
      const selectEl = container.querySelector(".agent-report-select");
      const nextReportsTo = (selectEl?.value || "").trim();
      if ((a.reports_to || "") === nextReportsTo) { showToast("No reporting change.", "info"); return; }
      send({ type: "update_agent", name: a.name, reports_to: nextReportsTo });
      agentsState.quickReportAgent = null;
      agentsState.expandedAgent    = null;
      agentsState.agentDetail      = null;
      renderAgentsPanel();
    });
  }

  slot.innerHTML = "";
  slot.appendChild(container);
}

// ── Tab switching ──────────────────────────────────────────────────────────

export function switchTab(tab) {
  // Settings tab: open modal without switching panels
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
      sendListMemories("", 50, false, 0, ["user_model", "project_knowledge", "decision"]);
      send({ type: "get_learning_state" });
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
  const footer = document.querySelector("footer");
  if (footer) footer.style.display = resolvedTab === "chat" ? "" : "none";
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
    sendListMemories("", 50, false, 0, ["user_model", "project_knowledge", "decision"]);
    send({ type: "get_learning_state" });
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
}

// ── Agents panel ──────────────────────────────────────────────────────────

export function populateAgents(items) {
  state.agents = items.length ? items : [{ name: "default", description: "" }];

  if (els.hierarchyOptions) {
    const commanders = (items || []).filter((a) => (a.role || "specialist") === "commander");
    els.hierarchyOptions.innerHTML = commanders
      .map((a) => `<option value="${escHtml(a.name)}"></option>`)
      .join("");
  }

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
  const allNames = (agentsState.items || []).map((x) => x.name);
  const commanderOptions = allNames.map((name) => `<option value="${escHtml(name)}">${escHtml(name)}</option>`).join("");
  const _capsToText = (arr) => Array.isArray(arr) ? arr.join(", ") : "";
  const _textToCaps = (txt) => (txt || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  if (agentsState.addingNew) {
    el.innerHTML = `
      <div class="agent-edit-form agent-edit-form-standalone">
        <div class="agent-edit-title">New Agent</div>
        <label>Name <input id="anew-name" type="text" placeholder="my-agent" autocomplete="off"></label>
        <label>Description <input id="anew-desc" type="text" placeholder="What does this agent do?" autocomplete="off"></label>
        <label>Role
          <select id="anew-role">
            <option value="specialist" selected>specialist</option>
            <option value="commander">commander</option>
          </select>
        </label>
        <label>Team <input id="anew-team" type="text" placeholder="market_intel" autocomplete="off"></label>
        <label>Reports To
          <select id="anew-reports-to">
            <option value="">(none)</option>
            ${commanderOptions}
          </select>
        </label>
        <label>Capabilities <input id="anew-caps" type="text" placeholder="competitor_watch, sentiment" autocomplete="off"></label>
        <label>Tools <span class="aedit-hint">comma-separated · blank = inherit global</span><input id="anew-tools" type="text" placeholder="recall, fetch_url" autocomplete="off"></label>
        <label>Model <input id="anew-model" type="text" placeholder="(leave blank to inherit)" autocomplete="off"></label>
        <label>System Prompt <textarea id="anew-system" rows="4" placeholder="You are…"></textarea></label>
        <label>Instructions <textarea id="anew-instr" rows="3" placeholder="Always reply in…"></textarea></label>
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
        role:          el.querySelector("#anew-role").value,
        team:          el.querySelector("#anew-team").value.trim(),
        reports_to:    el.querySelector("#anew-reports-to").value.trim(),
        capabilities:  _textToCaps(el.querySelector("#anew-caps").value),
        tools:         _textToCaps(el.querySelector("#anew-tools").value),
        model:         el.querySelector("#anew-model").value.trim(),
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
  const list = agentsState.items || [];
  const byParent = new Map();
  list.forEach((a) => {
    const parent = a.reports_to || "";
    if (!byParent.has(parent)) byParent.set(parent, []);
    byParent.get(parent).push(a);
  });
  const sortByName = (a, b) => (a.name || "").localeCompare(b.name || "");
  for (const arr of byParent.values()) arr.sort(sortByName);
  const renderAgentCard = (a, depth = 0) => {
    const isExpanded = agentsState.expandedAgent === a.name;
    const editBadge  = a.editable ? "" : ' <span class="agent-badge">config</span>';
    const safeDepth  = Math.max(0, Math.min(Number(depth || 0), 12));
    const directReports = (byParent.get(a.name) || []).length;
    const tipReports = (a.reports_to || "").trim();
    const tipTeam = (a.team || "").trim();
    const tipDesc = (a.description || "").trim() || "No description.";
    const tipOrg =
      directReports > 0
        ? `${directReports} direct report${directReports === 1 ? "" : "s"}`
        : "Leaf agent (no direct reports)";
    const tipSource = a.editable
      ? ""
      : `<dt>Source</dt><dd>Defined in config file (read-only here)</dd>`;

    const avatarHue = [...(a.name || "A")].reduce((acc, c) => acc + c.charCodeAt(0), 0) % 360;
    const avatarLetter = (a.name || "?")[0].toUpperCase();

    const card = document.createElement("div");
    card.className = "list-item agent-item org-card";
    card.dataset.nodeCard = a.name;
    card.innerHTML = `
      <div class="agent-card-tip" role="tooltip">
        <div class="agent-card-tip-name">${escHtml(a.name)}</div>
        <dl class="agent-card-tip-dl">
          <dt>Role</dt><dd>${escHtml(a.role || "specialist")}</dd>
          <dt>Description</dt><dd class="agent-card-tip-desc">${escHtml(tipDesc)}</dd>
          ${tipTeam ? `<dt>Team</dt><dd>${escHtml(tipTeam)}</dd>` : ""}
          ${tipReports ? `<dt>Reports to</dt><dd>${escHtml(tipReports)}</dd>` : `<dt>Reports to</dt><dd>—</dd>`}
          <dt>Hierarchy</dt><dd>Depth ${safeDepth} · ${escHtml(tipOrg)}</dd>
          ${tipSource}
        </dl>
      </div>
      <div class="agent-card-main">
        <div class="agent-item-header">
          <div class="agent-avatar" style="--avatar-hue:${avatarHue}">${escHtml(avatarLetter)}</div>
          <div class="agent-meta">
            <div class="agent-name-row">
              <span class="agent-item-name" title="${escHtml(a.name)}">${escHtml(a.name)}${editBadge}</span>
              <span class="agent-role-badge">${escHtml(a.role || "specialist")}</span>
            </div>
            <span class="agent-item-desc" title="${escHtml(tipDesc)}">${escHtml(a.description || "—")}</span>
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
        agentsState.expandedAgent    = null;
        agentsState.agentDetail      = null;
        agentsState.editingAgent     = null;
        agentsState.quickReportAgent = null;
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
        agentsState.expandedAgent    = a.name;
        agentsState.agentDetail      = null;
        agentsState.editingAgent     = null;
        agentsState.quickReportAgent = null;
        card.querySelector(".btn-agent-toggle").classList.add("is-open");
        _fillDetailSlot(card, a, null);
        send({ type: "get_agent", name: a.name });
        requestAnimationFrame(() =>
          card.scrollIntoView({ behavior: "smooth", block: "nearest" })
        );
      }
    });

    return card;
  };

  const byName = new Map(list.map((a) => [a.name, a]));
  const nameSet = new Set(allNames);
  const sortRoots = (a, b) => {
    const ar = (a.role || "specialist");
    const br = (b.role || "specialist");
    if (ar !== br) return ar === "commander" ? -1 : 1;
    return sortByName(a, b);
  };
  const roots = list
    .filter((a) => !a.reports_to || !nameSet.has(a.reports_to))
    .slice()
    .sort(sortRoots);

  const visible = [];
  const seen    = new Set();

  const chart = document.createElement("div");
  chart.className = "agent-org-chart";

  const renderTreeNode = (agent, depth = 0) => {
    if (!agent || seen.has(agent.name)) return null;
    seen.add(agent.name);
    visible.push({ node: agent, depth });

    const collapsed = !!(agentsState.collapsedChildren?.[agent.name]);
    const children  = collapsed ? [] : (byParent.get(agent.name) || []);
    const card      = renderAgentCard(agent, depth);

    if (children.length === 0) {
      return card;
    }

    const group = document.createElement("div");
    group.className = "org-node-group";
    group.dataset.groupRoot = agent.name;

    const selfEl = document.createElement("div");
    selfEl.className = "org-node-self";
    selfEl.appendChild(card);
    group.appendChild(selfEl);

    const childrenEl = document.createElement("div");
    childrenEl.className = "org-node-children";
    children.forEach((child) => {
      const childEl = renderTreeNode(child, depth + 1);
      if (childEl) childrenEl.appendChild(childEl);
    });
    group.appendChild(childrenEl);

    return group;
  };

  roots.forEach((root) => {
    const el2 = renderTreeNode(root, 0);
    if (el2) chart.appendChild(el2);
  });

  const orphans = list.filter((a) => !seen.has(a.name)).sort(sortByName);
  if (orphans.length) {
    const orphanWrap = document.createElement("div");
    orphanWrap.className = "org-orphan-row";
    orphans.forEach((a) => {
      seen.add(a.name);
      visible.push({ node: a, depth: 0 });
      orphanWrap.appendChild(renderAgentCard(a, 0));
    });
    chart.appendChild(orphanWrap);
  }

  // ── Highlight: dim unrelated nodes on hover ────────────────────────────────
  const highlightBranch = (focusName) => {
    if (!focusName) return;
    const childrenMap = new Map();
    const parentMap   = new Map();
    visible.forEach(({ node }) => {
      const parent = (node.reports_to || "").trim();
      if (!parent || !byName.has(parent) || !seen.has(parent)) return;
      if (!childrenMap.has(parent)) childrenMap.set(parent, []);
      childrenMap.get(parent).push(node.name);
      parentMap.set(node.name, parent);
    });

    const related = new Set([focusName]);

    let cur = focusName;
    while (true) {
      const p = parentMap.get(cur);
      if (!p || related.has(p)) break;
      related.add(p);
      cur = p;
    }

    const downQueue = [focusName];
    while (downQueue.length) {
      const node = downQueue.shift();
      (childrenMap.get(node) || []).forEach((c) => {
        if (!related.has(c)) { related.add(c); downQueue.push(c); }
      });
    }

    chart.classList.add("branch-focus");
    chart.querySelectorAll("[data-node-card]").forEach((cardEl) => {
      const name = cardEl.getAttribute("data-node-card") || "";
      cardEl.classList.toggle("is-related", related.has(name));
      cardEl.classList.toggle("is-focused", name === focusName);
    });
    chart.querySelectorAll(".org-node-group").forEach((groupEl) => {
      const root2 = groupEl.dataset.groupRoot || "";
      groupEl.classList.toggle("is-group-related", related.has(root2));
    });
  };

  const clearBranchHighlight = () => {
    chart.classList.remove("branch-focus");
    chart.querySelectorAll("[data-node-card]").forEach((cardEl) => {
      cardEl.classList.remove("is-related", "is-focused");
    });
    chart.querySelectorAll(".org-node-group").forEach((groupEl) => {
      groupEl.classList.remove("is-group-related");
    });
  };

  el.appendChild(chart);
  requestAnimationFrame(() => {
    chart.querySelectorAll("[data-node-card]").forEach((cardEl) => {
      const name = cardEl.getAttribute("data-node-card") || "";
      cardEl.addEventListener("mouseenter", () => highlightBranch(name));
      cardEl.addEventListener("mouseleave", () => clearBranchHighlight());
    });
  });
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
