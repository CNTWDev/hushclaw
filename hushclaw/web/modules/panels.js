/**
 * panels.js — Sessions sidebar, agents panel, memories panel, skills panel, tab switching.
 */

import {
  state, els, skills, agentsState,
  send, sendListMemories, escHtml, showSkillToast, showToast, setSending,
  isSessionRunning, getCurrentSessionId, setCurrentSessionId, clearCurrentSessionId, debugUiLifecycle,
} from "./state.js";
import { rehydrateInProgressUi, resetChatSessionUiState } from "./chat.js";
import { openConfirm, openDialog, closeModal } from "./modal.js";
import { renderLoadingMarkup } from "./loading.js";

const SESSIONS_COLLAPSED_KEY = "hushclaw.ui.sessions-collapsed";
const LAST_TAB_KEY = "hushclaw.ui.last-tab";
let _sessionsCollapsed = false;
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
        description:  container.querySelector("#aedit-desc")?.value,
        role:         container.querySelector("#aedit-role")?.value,
        team:         container.querySelector("#aedit-team")?.value,
        reports_to:   container.querySelector("#aedit-reports-to")?.value,
        capabilities: _textToCaps(container.querySelector("#aedit-caps")?.value),
        model:        container.querySelector("#aedit-model")?.value,
        system_prompt: container.querySelector("#aedit-system")?.value,
        instructions: container.querySelector("#aedit-instr")?.value,
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
  const tabBtn = document.querySelector(`.tab[data-tab="${tab}"]`);
  const tabPanel = document.getElementById(`panel-${tab}`);
  const resolvedTab = (tabBtn && tabPanel) ? tab : "chat";
  if (state.tab === resolvedTab) {
    // Keep URL/storage in sync even if caller repeats same tab.
    const targetHash = `#tab=${encodeURIComponent(resolvedTab)}`;
    if (location.hash !== targetHash) {
      history.replaceState(null, "", targetHash);
    }
    try { localStorage.setItem(LAST_TAB_KEY, resolvedTab); } catch { /* ignore */ }
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
  // Notify any registered plugins that a tab has been activated.
  import("./plugin-host.js").then(({ notifyTabActivated }) => notifyTabActivated(resolvedTab));
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
  if (resolvedTab === "memories") sendListMemories("", 20, true);
  if (resolvedTab === "agents") send({ type: "list_agents" });
  if (resolvedTab === "skills") {
    send({ type: "list_skills" });
    loadSkillMarketplace();
  }
  if (resolvedTab === "tasks") {
    send({ type: "list_todos" });
    send({ type: "list_scheduled_tasks" });
    // Import lazily to avoid circular dependency; tasks module handles its own populate.
    import("./tasks.js").then(({ populateSchedAgentSelect }) => populateSchedAgentSelect());
  }
}

// ── Agents panel ──────────────────────────────────────────────────────────

export function populateAgents(items) {
  state.agents = items.length ? items : [{ name: "default", description: "" }];

  // Keep run-hierarchy commander suggestions in sync with agent list.
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
        description: el.querySelector("#anew-desc").value.trim(),
        role: el.querySelector("#anew-role").value,
        team: el.querySelector("#anew-team").value.trim(),
        reports_to: el.querySelector("#anew-reports-to").value.trim(),
        capabilities: _textToCaps(el.querySelector("#anew-caps").value),
        model: el.querySelector("#anew-model").value.trim(),
        system_prompt: el.querySelector("#anew-system").value,
        instructions: el.querySelector("#anew-instr").value,
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

    // Deterministic hue from name for the avatar circle
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

    // If this card was already expanded (e.g. after a save-triggered re-render),
    // fill the slot immediately with whatever detail data we have.
    if (isExpanded) {
      _fillDetailSlot(card, a, agentsState.agentDetail);
    }

    // Toggle: expand/collapse in-place — no renderAgentsPanel() call.
    card.querySelector(".btn-agent-toggle").addEventListener("click", () => {
      if (agentsState.expandedAgent === a.name) {
        // Collapse this card.
        agentsState.expandedAgent    = null;
        agentsState.agentDetail      = null;
        agentsState.editingAgent     = null;
        agentsState.quickReportAgent = null;
        card.querySelector(".agent-detail-slot").innerHTML = "";
        card.querySelector(".btn-agent-toggle").classList.remove("is-open");
      } else {
        // Close the previously expanded card (if any) without a full re-render.
        if (agentsState.expandedAgent) {
          const prev = document.querySelector(
            `[data-node-card="${CSS.escape(agentsState.expandedAgent)}"]`
          );
          if (prev) {
            prev.querySelector(".agent-detail-slot").innerHTML = "";
            prev.querySelector(".btn-agent-toggle").classList.remove("is-open");
          }
        }
        // Expand this card.
        agentsState.expandedAgent    = a.name;
        agentsState.agentDetail      = null;
        agentsState.editingAgent     = null;
        agentsState.quickReportAgent = null;
        card.querySelector(".btn-agent-toggle").classList.add("is-open");
        _fillDetailSlot(card, a, null); // show loading spinner
        send({ type: "get_agent", name: a.name });
        // Scroll the card into view so it's always visible after expansion.
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

  // ── Recursive nested-block tree ───────────────────────────────────────────
  // Each node + its entire subtree forms a self-contained visual block.
  // Visual containment replaces SVG lines — no coordinate math, no crossings.

  const visible = [];        // all placed nodes, used by highlight logic
  const seen    = new Set(); // prevents double-placement (circular refs etc.)

  const chart = document.createElement("div");
  chart.className = "agent-org-chart";

  // Recursively render a node and its entire subtree as a nested block.
  // Parent card sits on the left; children are stacked vertically on the right.
  // No SVG or coordinate math — visual containment IS the hierarchy.
  const renderTreeNode = (agent, depth = 0) => {
    if (!agent || seen.has(agent.name)) return null;
    seen.add(agent.name);
    visible.push({ node: agent, depth });

    const collapsed = !!(agentsState.collapsedChildren?.[agent.name]);
    const children  = collapsed ? [] : (byParent.get(agent.name) || []);
    const card      = renderAgentCard(agent, depth);

    if (children.length === 0) {
      // Leaf (or collapsed): just the card, no wrapper needed.
      return card;
    }

    // Node with children: outer group box wraps card + children column.
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

  // Orphaned agents (e.g. circular refs) rendered as lone cards at the bottom.
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

    // Walk UP: direct ancestor chain only.
    let cur = focusName;
    while (true) {
      const p = parentMap.get(cur);
      if (!p || related.has(p)) break;
      related.add(p);
      cur = p;
    }

    // Walk DOWN: full subtree of focusName.
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
    // Highlight containing group boxes that belong to the related set.
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
  // Find the card that is currently expanded and fill its detail slot in-place.
  const cardEl = document.querySelector(`[data-node-card="${CSS.escape(name)}"]`);
  const agentObj = (agentsState.items || []).find((x) => x.name === name);
  if (cardEl && agentObj) {
    _fillDetailSlot(cardEl, agentObj, def);
  } else {
    // Fallback: card not in DOM yet (first render), do a full refresh.
    renderAgentsPanel();
  }
}

// ── Sessions sidebar ──────────────────────────────────────────────────────

export function loadSession(session_id) {
  setCurrentSessionId(session_id);
  document.querySelectorAll(".sidebar-session").forEach((el) => {
    el.classList.toggle("active", el.dataset.sessionId === session_id);
  });
  send({ type: "get_session_history", session_id });
}

export function renderSessions(items) {
  const list = document.getElementById("sessions-list");
  if (!list) return;
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<div class="empty-state" style="padding:12px;font-size:11px">No sessions</div>';
    state._firstSessionLoad = false;
    return;
  }

  items.forEach((s) => {
    const el = document.createElement("div");
    el.className = "sidebar-session" + (s.session_id === getCurrentSessionId() ? " active" : "");
    el.dataset.sessionId = s.session_id;

    const shortId = (s.session_id || "—").slice(-12);
    const title = (s.title || "").trim() || `Session ${shortId}`;
    const lastPreview = (s.last_preview || "").trim();
    const kind = s.kind || "chat";
    const kindLabel = kind === "scheduled" ? "SCHED" : (kind === "auto" ? "AUTO" : (kind === "broadcast" ? "CAST" : ""));
    const lastTs = s.last_turn
      ? new Date(s.last_turn * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
      : "";

    el.innerHTML = `
      <div class="sidebar-session-info">
        <div class="sidebar-session-title-row">
          <div class="sidebar-session-title" title="${escHtml(title)}">${escHtml(title)}</div>
          ${kindLabel ? `<span class="session-kind-badge">${kindLabel}</span>` : ""}
        </div>
        <div class="sidebar-session-meta">${s.turn_count || 0} turns${lastTs ? " · " + lastTs : ""} · ${escHtml(shortId)}</div>
        ${lastPreview ? `<div class="sidebar-session-preview">${escHtml(lastPreview)}</div>` : ""}
      </div>
      <button class="session-delete-btn" data-session-id="${escHtml(s.session_id || "")}" title="Delete session">✕</button>
    `;
    el.querySelector(".session-delete-btn").addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const sid = ev.currentTarget.dataset.sessionId;
      if (!sid) return;
      const confirmed = await openConfirm({
        title: "Delete session",
        message: `Delete this session (${sid.slice(-12)})? Chat history for it will be removed.`,
        confirmText: "Delete",
        cancelText: "Cancel",
        dangerConfirm: true,
      });
      if (!confirmed) return;
      send({ type: "delete_session", session_id: sid });
    });
    el.addEventListener("click", () => loadSession(s.session_id));
    list.appendChild(el);
  });
  state._firstSessionLoad = false;
}

function _applySessionsCollapsed(collapsed) {
  _sessionsCollapsed = !!collapsed;
  document.body.classList.toggle("sessions-collapsed", _sessionsCollapsed);
  if (els.btnToggleSess) {
    els.btnToggleSess.textContent = _sessionsCollapsed ? "⟩" : "⟨";
    els.btnToggleSess.title = _sessionsCollapsed ? "Expand sessions" : "Collapse sessions";
  }
  if (els.btnToggleSessInline) {
    els.btnToggleSessInline.classList.toggle("hidden", !_sessionsCollapsed);
  }
  try { localStorage.setItem(SESSIONS_COLLAPSED_KEY, _sessionsCollapsed ? "1" : "0"); } catch {}
}

export function toggleSessionsSidebar(forceCollapsed) {
  if (typeof forceCollapsed === "boolean") {
    _applySessionsCollapsed(forceCollapsed);
    return;
  }
  _applySessionsCollapsed(!_sessionsCollapsed);
}

export function initSessionsSidebarState() {
  let collapsed = false;
  try { collapsed = localStorage.getItem(SESSIONS_COLLAPSED_KEY) === "1"; } catch {}
  _applySessionsCollapsed(collapsed);
}

export function onSessionDeleted(sessionId, ok) {
  if (!ok) { showToast(`Failed to delete session: ${sessionId}`, "err"); return; }
  const el = document.querySelector(`#sessions-list [data-session-id="${CSS.escape(sessionId)}"]`);
  if (el) el.remove();
  if (getCurrentSessionId() === sessionId) {
    clearCurrentSessionId();
    resetChatSessionUiState();
  }
}

// ── Memories panel ────────────────────────────────────────────────────────

export function renderMemories(items) {
  els.memoriesList.innerHTML = "";
  if (els.memoriesCount) {
    els.memoriesCount.textContent = items.length ? String(items.length) : "";
  }
  if (!items.length) {
    els.memoriesList.innerHTML = '<div class="empty-state">No memories found.</div>';
    return;
  }
  const list = document.createElement("div");
  list.className = "mem-list";
  const fmtTs = (raw) => {
    const n = Number(raw || 0);
    if (!Number.isFinite(n) || n <= 0) return "";
    return new Date(n * 1000).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  };
  items.forEach((m) => {
    const noteId = String(m.note_id ?? m.id ?? "").trim();
    const title  = m.title || m.content || m.text || "";
    const body   = m.body ? m.body.slice(0, 160) + (m.body.length > 160 ? "…" : "") : "";
    const rawTags = (m.tags || []).filter(t => t && !t.startsWith("_"));
    const tagsHtml = rawTags.length
      ? rawTags.map(t => `<span class="mem-tag">${escHtml(t)}</span>`).join("")
      : "";
    const scoreHtml = m.score != null
      ? `<span class="mem-score">${m.score.toFixed(2)}</span>`
      : "";
    const dateStr = fmtTs(m.created_at || m.created || 0);
    const dateHtml = dateStr ? `<span class="mem-date">${escHtml(dateStr)}</span>` : "";
    const footerItems = [tagsHtml, scoreHtml, dateHtml].filter(Boolean).join("");

    const card = document.createElement("div");
    card.className = "mem-card";
    card.dataset.noteId = noteId;
    card.innerHTML = `
      <div class="mem-card-left" title="Click to view full memory">
        <div class="mem-card-title">${escHtml(title)}</div>
        ${body ? `<div class="mem-card-body">${escHtml(body)}</div>` : ""}
        ${footerItems ? `<div class="mem-card-footer">${footerItems}</div>` : ""}
      </div>
      <div class="mem-card-right">
        <button class="mem-delete-btn icon-btn" data-note-id="${escHtml(noteId)}" title="Delete memory">✕</button>
      </div>
    `;

    // Click on the content area → open full-detail modal.
    card.querySelector(".mem-card-left").addEventListener("click", () => {
      const fullBody = m.body || m.content || "";
      const allTags  = (m.tags || []).filter(Boolean);
      const dateStr2 = fmtTs(m.created_at || m.created || 0);
      const tagsHtml2 = allTags.length
        ? `<div class="mem-modal-tags">${allTags.map(t => `<span class="mem-tag">${escHtml(t)}</span>`).join("")}</div>`
        : "";
      const metaHtml = [
        dateStr2  ? `<span class="mem-date">${escHtml(dateStr2)}</span>` : "",
        m.score != null ? `<span class="mem-score">${m.score.toFixed(3)}</span>` : "",
        `<span class="mem-id" title="note_id">${escHtml(noteId)}</span>`,
      ].filter(Boolean).join("");

      openDialog({
        title: title.slice(0, 100) || "Memory",
        html: `
          ${tagsHtml2 ? `<div class="mem-modal-meta">${tagsHtml2}<div class="mem-modal-info">${metaHtml}</div></div>` : (metaHtml ? `<div class="mem-modal-meta"><div class="mem-modal-info">${metaHtml}</div></div>` : "")}
          <pre class="mem-modal-body">${escHtml(fullBody || "—")}</pre>`,
        actions: [
          {
            label: "Delete",
            secondary: true,
            danger: true,
            onClick: async () => {
              const confirmed = await openConfirm({
                title: "Delete memory",
                message: `Delete "${title.slice(0, 60)}${title.length > 60 ? "…" : ""}"?`,
                confirmText: "Delete",
                cancelText: "Cancel",
                dangerConfirm: true,
              });
              if (confirmed) {
                send({ type: "delete_memory", note_id: noteId });
                closeModal();
              }
            },
          },
          { label: "Close", secondary: true, onClick: () => closeModal() },
        ],
      });
    });

    card.querySelector(".mem-delete-btn").addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const nid = ev.currentTarget.dataset.noteId;
      if (!nid) return;
      const confirmed = await openConfirm({
        title: "Delete memory",
        message: `Delete "${title.slice(0, 60)}${title.length > 60 ? "…" : ""}"?`,
        confirmText: "Delete",
        cancelText: "Cancel",
        dangerConfirm: true,
      });
      if (confirmed) send({ type: "delete_memory", note_id: nid });
    });
    list.appendChild(card);
  });
  els.memoriesList.appendChild(list);
}

export function onMemoryDeleted(noteId, ok) {
  if (!ok) {
    showToast(`Failed to delete memory: ${noteId != null ? noteId : ""}`, "err");
    return;
  }
  // Re-fetch list so (1) UI matches DB and (2) a stale in-flight list_memories response
  // cannot re-render the deleted row after we removed it from the DOM.
  sendListMemories(els.memorySearch?.value?.trim() || "", 20, true);
}

// ── Skills panel ───────────────────────────────────────────────────────────

export function loadSkillMarketplace() {
  skills.reposLoading = true;
  skills.reposError = "";
  renderSkillsPanel();
  send({ type: "list_skill_repos" });
}

export function handleSkillsList(data) {
  skills.installed = data.items || [];
  skills.skillDir  = data.skill_dir || "";
  skills.userSkillDir = data.user_skill_dir || "";
  skills.configured = Boolean(data.configured);
  if (els.skillDirBadge) {
    els.skillDirBadge.textContent = skills.skillDir
      ? `skill_dir: ${skills.skillDir}`
      : "skill_dir: not configured";
  }
  renderSkillsPanel();
}

export function handleSkillRepos(data) {
  skills.reposLoading = false;
  skills.repos = data.items || [];
  skills.categories = data.categories || [];
  skills.reposError = data.error || "";
  skills.activeCategory = "All";
  renderSkillsPanel();
}

export function handleSkillInstallResult(data) {
  skills.installing.delete(data.url);
  if (data.ok) {
    if (data.warning) {
      showSkillToast(`⚠ ${data.repo} cloned — ${data.warning}`, "warn");
    } else {
      const added = data.repo_skill_count != null ? data.repo_skill_count : data.skill_count;
      const toolsMsg = data.bundled_tool_count ? `, ${data.bundled_tool_count} tools loaded` : "";
      const depsMsg = data.deps_installed === false ? " (deps install failed, check manually)" : "";
      showSkillToast(`✓ ${data.repo} installed (${added} new skills${toolsMsg})${depsMsg}`, "ok");
    }
    send({ type: "list_skills" });
    send({ type: "list_skill_repos" });
  } else {
    showSkillToast(`Error: ${data.error}`, "err");
  }
  renderSkillsPanel();
}

export function publishSkill(skillName, skillDesc, repoUrl) {
  send({ type: "publish_skill", skill_name: skillName, skill_description: skillDesc || "", repo_url: repoUrl || "" });
}

export function handlePublishSkillUrl(data) {
  if (!data.ok) {
    showSkillToast(`Publish error: ${data.error}`, "err");
    return;
  }
  window.open(data.url, "_blank", "noopener");
  showSkillToast(`Opening GitHub to publish "${data.skill_name}"…`, "ok");
}

export function installSkillRepo(url) {
  if (!url || skills.installing.has(url)) return;
  skills.installing.add(url);
  renderSkillsPanel();
  send({ type: "install_skill_repo", url });
}

export function renderSkillsPanel() {
  if (!els.skillsContent) return;
  const c = els.skillsContent;
  c.innerHTML = "";

  // Skill-first discoverability hint
  const hint = document.createElement("p");
  hint.className = "skills-discovery-hint";
  hint.innerHTML = "Skills extend the agent\u2019s capabilities without extra agents. Install a skill and the agent will use it automatically \u2014 no orchestration setup needed.";
  c.appendChild(hint);

  const sec1 = document.createElement("div");
  sec1.className = "skills-section";

  let installedHtml = `<div class="skills-section-header">Installed Skills <span class="skills-count">${skills.installed.length}</span></div>`;

  if (!skills.configured) {
    installedHtml += `
      <div class="skill-notice">
        <strong>skill_dir not configured.</strong><br>
        Configure <code>tools.skill_dir</code> (or <code>tools.user_skill_dir</code>) in <code>hushclaw.toml</code> to enable skills:
        <pre>[tools]\nskill_dir = "/absolute/path/to/skills"</pre>
      </div>`;
  } else if (!skills.installed.length) {
    installedHtml += `<div class="empty-state" style="padding:16px 0">No skills installed yet. Browse the marketplace below.</div>`;
  } else {
    const scopeOrder = ["builtin", "system", "user", "workspace", "memory", "unknown"];
    const scopeNames = {
      builtin: "Built-in Skills",
      system: "System Directory Skills",
      user: "User Directory Skills",
      workspace: "Workspace Skills",
      memory: "Memory Skills",
      unknown: "Unclassified Skills",
    };
    const groups = new Map();
    skills.installed.forEach((s) => {
      const scope = s.scope || (s.builtin ? "builtin" : "unknown");
      if (!groups.has(scope)) groups.set(scope, []);
      groups.get(scope).push(s);
    });

    installedHtml += `<div class="skills-installed-list">`;
    scopeOrder.forEach((scope) => {
      const arr = groups.get(scope) || [];
      if (!arr.length) return;
      installedHtml += `<div class="skills-scope-header">${escHtml(scopeNames[scope] || scope)}</div>`;
      arr.forEach((s) => {
      const available = s.available !== false;
      const unavailBadge = available ? "" :
        `<span class="skill-badge-unavailable" title="${escHtml(s.reason || "Requirements not met")}">⚠ Unavailable</span>`;
      const unavailReason = (!available && s.reason)
        ? `<div class="skill-reason">${escHtml(s.reason)}</div>` : "";
      const installHints = (!available && s.install_hints && s.install_hints.length)
        ? s.install_hints.map(h =>
            `<div class="skill-install-hint">Run: <code class="skill-install-cmd" title="Click to copy" onclick="navigator.clipboard.writeText(${JSON.stringify(h.cmd)}).then(()=>{this.classList.add('copied');setTimeout(()=>this.classList.remove('copied'),1500)})">${escHtml(h.cmd)}</code></div>`
          ).join("")
        : "";
        installedHtml += `
        <div class="skill-installed-item${available ? "" : " skill-unavailable"}">
          <div class="skill-installed-meta">
            <span class="skill-name">${escHtml(s.name)}</span>
            ${unavailBadge}
            ${s.description ? `<span class="skill-desc">${escHtml(s.description)}</span>` : ""}
            ${unavailReason}
            ${installHints}
          </div>
          ${s.builtin ? "" : `<button class="secondary skill-publish-btn" data-name="${escHtml(s.name)}" data-desc="${escHtml(s.description || "")}">Publish</button>`}
        </div>`;
      });
    });
    installedHtml += `</div>`;
  }
  sec1.innerHTML = installedHtml;
  c.appendChild(sec1);

  const sec2 = document.createElement("div");
  sec2.className = "skills-section";

  let mktHtml = `
    <div class="skills-section-header">
      Skill Marketplace
      <button class="secondary skill-mkt-refresh-btn" id="btn-skill-mkt-refresh">↻ Refresh</button>
    </div>`;

  if (skills.reposLoading) {
    mktHtml += `<div class="empty-state" style="padding:24px 0">Searching GitHub…</div>`;
  } else {
    if (skills.reposError) {
      mktHtml += `<div class="skill-notice skill-notice-warn">GitHub search unavailable (${escHtml(skills.reposError)}). Showing curated repos.</div>`;
    }

    if (skills.categories.length) {
      const cats = ["All", ...skills.categories.map(cat => cat.name)];
      mktHtml += `<div class="cat-tab-bar" id="cat-tab-bar">`;
      cats.forEach(name => {
        const active = name === skills.activeCategory ? " active" : "";
        mktHtml += `<button class="cat-tab${active}" data-cat="${escHtml(name)}">${escHtml(name)}</button>`;
      });
      mktHtml += `</div>`;
    }

    const activeCatNames = skills.activeCategory === "All" ? null
      : new Set((skills.categories.find(cat => cat.name === skills.activeCategory)?.skills || []).map(s => s.name));

    mktHtml += `<div class="skill-repo-list" id="skill-repo-list">`;
    skills.repos.forEach((repo) => {
      const installing = skills.installing.has(repo.url);
      const isIndex    = Boolean(repo.note);
      const btnText    = installing ? "…" : (repo.installed ? "Update" : "Install");
      const btnClass   = repo.installed ? "secondary" : "";
      const curatedBadge = repo.curated ? `<span class="skill-curated-badge">Curated</span>` : "";
      const starsHtml    = repo.stars ? `<div class="stars-badge">★ ${Number(repo.stars).toLocaleString()}</div>` : "";
      const authorHtml   = repo.author ? `<span class="repo-card-author">by ${escHtml(repo.author)}</span>` : "";
      const tagsHtml     = (repo.tags && repo.tags.length)
        ? `<div class="repo-card-tags">${repo.tags.map(t => `<span class="repo-tag">${escHtml(t)}</span>`).join("")}</div>`
        : "";
      const hidden = activeCatNames && !activeCatNames.has(repo.name) ? ' style="display:none"' : "";
      mktHtml += `
        <div class="skill-repo-card" data-name="${escHtml(repo.name)}"${hidden}>
          <div class="repo-card-left">
            <div class="repo-card-name">
              ${curatedBadge}
              <a href="${escHtml(repo.html_url)}" target="_blank" rel="noopener">${escHtml(repo.name)}</a>
              ${authorHtml}
            </div>
            ${repo.description ? `<div class="repo-card-desc">${escHtml(repo.description)}</div>` : ""}
            ${tagsHtml}
            ${repo.note ? `<div class="repo-card-note">ℹ ${escHtml(repo.note)}</div>` : ""}
          </div>
          <div class="repo-card-right">
            ${starsHtml}
            <div class="repo-card-actions">
              ${repo.installed ? '<span class="skill-installed-badge">✓</span>' : ""}
              ${isIndex
                ? `<a href="${escHtml(repo.html_url)}" target="_blank" rel="noopener" class="secondary repo-install-btn">Browse</a>`
                : `<button class="${btnClass} repo-install-btn" data-url="${escHtml(repo.url)}" ${installing ? "disabled" : ""}>${escHtml(btnText)}</button>`
              }
            </div>
          </div>
        </div>`;
    });
    mktHtml += `</div>`;
    if (!skills.repos.length) {
      mktHtml += `<div class="empty-state" style="padding:24px 0">No skill repos found. Use the custom URL below.</div>`;
    }
  }

  mktHtml += `
    <div class="skill-custom-install">
      <div class="skill-custom-label">Add custom repo</div>
      <div class="skill-custom-row">
        <input type="text" id="skill-custom-url"
               placeholder="https://github.com/user/my-skills"
               autocomplete="off">
        <button id="btn-install-custom">Install</button>
      </div>
    </div>`;

  sec2.innerHTML = mktHtml;
  c.appendChild(sec2);

  document.getElementById("btn-skill-mkt-refresh")
    ?.addEventListener("click", loadSkillMarketplace);

  sec2.querySelectorAll(".cat-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      skills.activeCategory = btn.dataset.cat;
      sec2.querySelectorAll(".cat-tab").forEach(b => b.classList.toggle("active", b.dataset.cat === skills.activeCategory));
      const catEntry = skills.categories.find(cat => cat.name === skills.activeCategory);
      const catNames = catEntry ? new Set(catEntry.skills.map(s => s.name)) : null;
      sec2.querySelectorAll(".skill-repo-card").forEach((card) => {
        const visible = !catNames || catNames.has(card.dataset.name);
        card.style.display = visible ? "" : "none";
      });
    });
  });

  document.getElementById("btn-install-custom")
    ?.addEventListener("click", () => {
      const url = document.getElementById("skill-custom-url")?.value.trim();
      if (url) installSkillRepo(url);
    });

  document.getElementById("skill-custom-url")
    ?.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.isComposing) {
        const url = ev.target.value.trim();
        if (url) installSkillRepo(url);
      }
    });

  sec2.querySelectorAll(".repo-install-btn").forEach((btn) => {
    btn.addEventListener("click", () => installSkillRepo(btn.dataset.url));
  });

  sec1.querySelectorAll(".skill-publish-btn").forEach((btn) => {
    btn.addEventListener("click", () => publishSkill(btn.dataset.name, btn.dataset.desc, ""));
  });
}
