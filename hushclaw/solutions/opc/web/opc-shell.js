const state = {
  ws: null,
  mode: "chat",
  agents: [],
  employees: [],
  employeeDrafts: [],
  skillRecommendations: [],
  teams: [],
  channels: [],
  goals: [],
  workItems: [],
  messagesByChannel: {},
  selectedChannelId: "",
  selectedGoalId: "",
  selectedDraftId: "",
  editingTeamId: "",
  editingGoalId: "",
  editingEmployeeId: "",
  sending: false,
};

const $ = (id) => document.getElementById(id);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams(location.search);
  const key = params.get("api_key") || "";
  const q = key ? `?api_key=${encodeURIComponent(key)}` : "";
  return `${proto}//${location.host || "127.0.0.1:8765"}${q}`;
}

function send(payload) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  state.ws.send(JSON.stringify(payload));
}

function connect() {
  setStatus("connecting");
  const ws = new WebSocket(wsUrl());
  state.ws = ws;
  ws.onopen = () => {
    setStatus("connected");
    refreshAll();
  };
  ws.onmessage = (event) => {
    let data;
    try { data = JSON.parse(event.data); } catch { return; }
    handleMessage(data);
  };
  ws.onclose = () => {
    setStatus("disconnected");
    setTimeout(connect, 1500);
  };
  ws.onerror = () => ws.close();
}

function refreshAll() {
  send({ type: "list_agents" });
  send({ type: "opc_get_overview" });
  send({ type: "opc_list_channels" });
  send({ type: "opc_list_employee_drafts" });
}

function handleMessage(data) {
  switch (data.type) {
    case "agents":
      state.agents = data.items || [];
      render();
      break;
    case "opc_overview":
      state.employees = data.employees || [];
      state.employeeDrafts = data.employee_drafts || state.employeeDrafts || [];
      state.skillRecommendations = data.skill_recommendations || state.skillRecommendations || [];
      state.teams = data.teams || [];
      state.channels = data.channels || state.channels;
      state.goals = data.goals || [];
      state.workItems = data.work_items || [];
      ensureSelectedChannel();
      render();
      break;
    case "opc_employee_drafts":
      state.employeeDrafts = data.items || [];
      state.skillRecommendations = data.skill_recommendations || [];
      render();
      break;
    case "opc_employee_draft":
      state.employeeDrafts = data.items || [];
      state.skillRecommendations = data.skill_recommendations || [];
      state.selectedDraftId = data.item?.id || state.selectedDraftId;
      renderEmployeeDraftPreview(data.item);
      render();
      break;
    case "opc_employee_created":
      state.employees = data.employees || state.employees;
      state.teams = data.teams || state.teams;
      state.channels = data.channels || state.channels;
      state.skillRecommendations = data.skill_recommendations || state.skillRecommendations;
      state.selectedDraftId = "";
      $("btn-create-employee").disabled = true;
      $("employee-dialog").close();
      refreshAll();
      break;
    case "opc_employee_skill_approved":
      state.skillRecommendations = data.skill_recommendations || state.skillRecommendations;
      renderEmployeeDraftPreview(currentDraft());
      break;
    case "opc_channels":
      state.channels = data.items || [];
      ensureSelectedChannel();
      render();
      loadCurrentHistory();
      break;
    case "opc_channel_history":
      state.messagesByChannel[data.channel_id] = data.items || [];
      renderMessages();
      break;
    case "opc_channel_message_result":
      state.sending = false;
      state.messagesByChannel[data.channel?.id || state.selectedChannelId] = data.messages || [];
      $("composer-input").disabled = false;
      render();
      break;
    case "opc_team_saved":
    case "opc_channel_saved":
    case "opc_goal_saved":
    case "opc_employee_saved":
    case "opc_goal_plan":
    case "opc_goal_approved":
    case "opc_discussion":
      state.sending = false;
      refreshAll();
      break;
    case "error":
      state.sending = false;
      $("composer-input").disabled = false;
      $("btn-generate-employee").disabled = false;
      $("btn-create-employee").disabled = currentDraft()?.status !== "draft";
      appendLocalSystemMessage(data.message || "Unknown error");
      renderMessages();
      break;
    default:
      break;
  }
}

function ensureSelectedChannel() {
  if (state.selectedChannelId && state.channels.some((item) => item.id === state.selectedChannelId)) return;
  state.selectedChannelId = state.channels[0]?.id || "";
}

function loadCurrentHistory() {
  if (!state.selectedChannelId) return;
  send({ type: "opc_get_channel_history", channel_id: state.selectedChannelId, limit: 100 });
}

function setMode(mode) {
  state.mode = mode;
  $$(".rail-item, .tab").forEach((el) => el.classList.toggle("active", el.dataset.mode === mode));
  $$(".mode-panel").forEach((el) => el.classList.toggle("active", el.id === `mode-${mode}`));
}

function render() {
  renderChannels();
  renderEmployees();
  renderSelects();
  renderHeader();
  renderMessages();
  renderGoals();
  renderWork();
  renderContext();
}

function renderChannels() {
  $("channel-list").innerHTML = state.channels.length
    ? state.channels.map((channel) => `
      <button class="channel-item ${channel.id === state.selectedChannelId ? "active" : ""}" data-channel-id="${esc(channel.id)}">
        <span>#</span>
        <strong>${esc(channel.name)}</strong>
      </button>
    `).join("")
    : `<div class="empty">Create a team to open a channel.</div>`;
}

function renderEmployees() {
  const drafts = state.employeeDrafts.filter((item) => item.status === "draft");
  $("employee-list").innerHTML = [
    ...state.employees.map((item) => `
      <div class="employee-row">
      <button type="button" class="employee-item" data-mention="${esc(item.agent_name)}">
        <span class="avatar">${esc((item.display_name || item.agent_name || "?").slice(0, 1).toUpperCase())}</span>
        <span>
          <strong>${esc(item.display_name || item.agent_name)}</strong>
          <small>${esc(item.role || "specialist")}</small>
        </span>
      </button>
        <button type="button" class="row-action" title="Edit employee" data-edit-employee="${esc(item.id)}">Edit</button>
        <button type="button" class="row-action danger" title="Archive employee" data-archive-employee="${esc(item.id)}">Archive</button>
      </div>
    `),
    ...drafts.map((item) => `
      <div class="employee-row">
      <button type="button" class="employee-item draft" data-open-draft="${esc(item.id)}">
        <span class="avatar">?</span>
        <span>
          <strong>${esc(item.display_name || item.agent_name)}</strong>
          <small>draft</small>
        </span>
      </button>
        <button type="button" class="row-action danger" title="Delete draft" data-delete-draft="${esc(item.id)}">Delete</button>
      </div>
    `),
  ].join("") || `<div class="empty">No digital employees.</div>`;
}

function renderHeader() {
  const channel = currentChannel();
  $("channel-title").textContent = channel ? `# ${channel.name}` : "OPC";
}

function renderMessages() {
  const messages = state.messagesByChannel[state.selectedChannelId] || [];
  const el = $("message-list");
  if (!state.selectedChannelId) {
    el.innerHTML = `<div class="empty large">Create a team channel to start operating with digital employees.</div>`;
    return;
  }
  el.innerHTML = messages.length
    ? messages.map(renderMessage).join("")
    : `<div class="empty large">No messages yet. Try <code>@all review this goal</code>.</div>`;
  el.scrollTop = el.scrollHeight;
}

function renderMessage(item) {
  const sender = item.sender_type === "agent"
    ? item.agent_name
    : item.sender_type === "system"
      ? "OPC"
      : "you";
  const cls = item.sender_type || "user";
  return `
    <article class="message ${esc(cls)}">
      <div class="message-avatar">${esc(String(sender || "?").slice(0, 1).toUpperCase())}</div>
      <div class="message-body">
        <div class="message-head">
          <strong>${esc(sender)}</strong>
          <time>${formatTime(item.created)}</time>
        </div>
        <div class="message-text">${formatText(item.text || "")}</div>
      </div>
    </article>
  `;
}

function renderGoals() {
  $("goal-list").innerHTML = state.goals.length
    ? state.goals.map((goal) => `
      <article class="work-card ${goal.id === state.selectedGoalId ? "active" : ""}" data-goal-id="${esc(goal.id)}">
        <div class="work-title">${esc(goal.objective)}</div>
        <div class="work-meta">${esc(goal.status || "draft")} · ${esc(teamName(goal.team_id) || "no team")}</div>
        <div class="work-actions">
          <button class="secondary" data-plan-goal="${esc(goal.id)}">Plan</button>
          <button class="primary" data-approve-goal="${esc(goal.id)}">Approve</button>
        </div>
      </article>
    `).join("")
    : `<div class="empty">No goals yet.</div>`;
}

function renderWork() {
  const html = state.workItems.length
    ? state.workItems.map((item) => `
      <article class="work-card">
        <div class="work-title">${esc(item.title || "Work item")}</div>
        <div class="work-meta">${esc(item.status || "draft")} · ${esc(item.assigned_agent || "unassigned")}</div>
        <p>${esc(item.notes || "")}</p>
      </article>
    `).join("")
    : `<div class="empty">No work items yet.</div>`;
  $("work-list").innerHTML = html;
  $("task-list").innerHTML = html;
}

function renderContext() {
  const team = currentTeam();
  const goal = state.goals.find((item) => item.id === state.selectedGoalId);
  $("team-context").innerHTML = team ? `
    <div class="context-title">${esc(team.name)}</div>
    <p>${esc(team.purpose || "No purpose set.")}</p>
    <div class="pill-row">${(team.member_agents || []).map((name) => `<button class="pill" data-mention="${esc(name)}">@${esc(name)}</button>`).join("")}</div>
    <div class="inline-actions">
      <button type="button" class="secondary" data-edit-team="${esc(team.id)}">Edit</button>
      <button type="button" class="secondary danger" data-archive-team="${esc(team.id)}">Archive</button>
    </div>
  ` : "No team selected.";
  $("goal-context").innerHTML = goal ? `
    <div class="context-title">${esc(goal.objective)}</div>
    <p>${esc(goal.success_criteria || "No success criteria.")}</p>
    <span class="pill">${esc(goal.status || "draft")}</span>
    <div class="inline-actions">
      <button type="button" class="secondary" data-edit-goal="${esc(goal.id)}">Edit</button>
      <button type="button" class="secondary" data-complete-goal="${esc(goal.id)}">Done</button>
      <button type="button" class="secondary danger" data-archive-goal="${esc(goal.id)}">Archive</button>
    </div>
  ` : "No linked goal.";
}

function renderSelects() {
  const agentOptions = state.agents.map((agent) => `<option value="${esc(agent.name)}">${esc(agent.name)}</option>`).join("");
  $$("select[name='facilitator']").forEach((el) => { el.innerHTML = `<option value="">Facilitator</option>${agentOptions}`; });
  $$("select[name='member_agents']").forEach((el) => { el.innerHTML = agentOptions; });
  const teamOptions = state.teams.map((team) => `<option value="${esc(team.id)}">${esc(team.name)}</option>`).join("");
  $$("select[name='team_id']").forEach((el) => { el.innerHTML = `<option value="">Team</option>${teamOptions}`; });
  $("goal-link").innerHTML = `<option value="">No goal</option>` + state.goals.map((goal) => `<option value="${esc(goal.id)}">${esc(short(goal.objective, 40))}</option>`).join("");
}

function currentChannel() {
  return state.channels.find((item) => item.id === state.selectedChannelId);
}

function currentTeam() {
  const channel = currentChannel();
  return state.teams.find((item) => item.id === channel?.team_id);
}

function submitMessage() {
  const input = $("composer-input");
  const text = input.value.trim();
  if (!text || !state.selectedChannelId || state.sending) return;
  state.sending = true;
  input.disabled = true;
  appendLocalUserMessage(text);
  send({
    type: "opc_send_channel_message",
    channel_id: state.selectedChannelId,
    text,
    goal_id: $("goal-link").value || "",
  });
  input.value = "";
  autoSize(input);
  renderMessages();
}

function appendLocalUserMessage(text) {
  const id = `local-${Date.now()}`;
  const item = {
    id,
    channel_id: state.selectedChannelId,
    sender_type: "user",
    text,
    created: Math.floor(Date.now() / 1000),
  };
  state.messagesByChannel[state.selectedChannelId] = [
    ...(state.messagesByChannel[state.selectedChannelId] || []),
    item,
  ];
}

function appendLocalSystemMessage(text) {
  const item = {
    id: `local-system-${Date.now()}`,
    channel_id: state.selectedChannelId,
    sender_type: "system",
    text,
    created: Math.floor(Date.now() / 1000),
  };
  state.messagesByChannel[state.selectedChannelId] = [
    ...(state.messagesByChannel[state.selectedChannelId] || []),
    item,
  ];
}

function submitTeam(form) {
  const data = new FormData(form);
  const teamId = data.get("team_id") || state.editingTeamId || "";
  const payload = {
    name: data.get("name"),
    purpose: data.get("purpose"),
    facilitator: data.get("facilitator"),
    member_agents: selectedValues(form.querySelector("select[name='member_agents']")),
  };
  send({
    type: teamId ? "opc_update_team" : "opc_create_team",
    team_id: teamId,
    team: payload,
  });
  state.editingTeamId = "";
  form.reset();
  $("team-dialog").close();
}

function submitGoal(form) {
  const data = new FormData(form);
  const goalId = data.get("goal_id") || state.editingGoalId || "";
  const payload = {
    objective: data.get("objective"),
    success_criteria: data.get("success_criteria"),
    team_id: data.get("team_id") || currentTeam()?.id || "",
    priority: Number(data.get("priority") || 0),
  };
  send({
    type: goalId ? "opc_update_goal" : "opc_create_goal",
    goal_id: goalId,
    goal: payload,
  });
  state.editingGoalId = "";
  form.reset();
  $("goal-dialog").close();
}

function submitEmployeeEdit(form) {
  const data = new FormData(form);
  const employeeId = data.get("employee_id") || state.editingEmployeeId || "";
  send({
    type: "opc_update_employee",
    employee_id: employeeId,
    employee: {
      display_name: data.get("display_name"),
      role: data.get("role"),
      description: data.get("description"),
      team: data.get("team"),
      reports_to: data.get("reports_to"),
      capabilities: splitList(data.get("capabilities")),
    },
  });
  state.editingEmployeeId = "";
  form.reset();
  $("employee-edit-dialog").close();
}

function submitEmployeeDraft(form) {
  const data = new FormData(form);
  $("btn-generate-employee").disabled = true;
  renderEmployeeDraftPreview(null, "Generating employee draft...");
  send({
    type: "opc_draft_employee",
    requirement: data.get("requirement"),
    team_id: data.get("team_id") || currentTeam()?.id || "",
  });
}

function createEmployeeFromDraft() {
  if (!state.selectedDraftId) return;
  $("btn-create-employee").disabled = true;
  send({ type: "opc_create_employee_from_draft", draft_id: state.selectedDraftId });
}

function closeDialog(button) {
  const dialog = button.closest("dialog");
  if (dialog) dialog.close();
}

function openTeamDialog(team = null) {
  const form = $("team-form");
  form.reset();
  state.editingTeamId = team?.id || "";
  $("team-dialog-title").textContent = team ? "Edit Team Channel" : "Create Team Channel";
  form.elements.team_id.value = team?.id || "";
  form.elements.name.value = team?.name || "";
  form.elements.purpose.value = team?.purpose || "";
  renderSelects();
  form.elements.facilitator.value = team?.facilitator || "";
  setSelectedValues(form.querySelector("select[name='member_agents']"), team?.member_agents || []);
  $("team-dialog").showModal();
}

function openGoalDialog(goal = null) {
  const form = $("goal-form");
  form.reset();
  state.editingGoalId = goal?.id || "";
  $("goal-dialog-title").textContent = goal ? "Edit Goal" : "Create Goal";
  form.elements.goal_id.value = goal?.id || "";
  form.elements.objective.value = goal?.objective || "";
  form.elements.success_criteria.value = goal?.success_criteria || "";
  form.elements.team_id.value = goal?.team_id || currentTeam()?.id || "";
  form.elements.priority.value = goal?.priority ?? 1;
  $("goal-dialog").showModal();
}

function openEmployeeEditDialog(employee) {
  if (!employee) return;
  const form = $("employee-edit-form");
  form.reset();
  state.editingEmployeeId = employee.id || "";
  form.elements.employee_id.value = employee.id || "";
  form.elements.display_name.value = employee.display_name || employee.agent_name || "";
  form.elements.role.value = employee.role || "";
  form.elements.description.value = employee.description || "";
  form.elements.team.value = employee.team || "";
  form.elements.reports_to.value = employee.reports_to || "";
  form.elements.capabilities.value = (employee.capabilities || []).join("\n");
  $("employee-edit-dialog").showModal();
}

function archiveRecord(type, id) {
  const labels = {
    team: "Archive this team and hide its channel?",
    goal: "Archive this goal?",
    employee: "Archive this digital employee?",
    draft: "Delete this draft?",
  };
  if (!window.confirm(labels[type] || "Continue?")) return;
  if (type === "team") send({ type: "opc_archive_team", team_id: id });
  if (type === "goal") send({ type: "opc_archive_goal", goal_id: id });
  if (type === "employee") send({ type: "opc_archive_employee", employee_id: id });
  if (type === "draft") send({ type: "opc_delete_employee_draft", draft_id: id });
}

function approveSkillRecommendation(recommendationId) {
  if (!state.selectedDraftId) return;
  send({
    type: "opc_approve_employee_skill",
    draft_id: state.selectedDraftId,
    recommendation_id: recommendationId,
  });
}

function renderEmployeeDraftPreview(draft, pendingText = "") {
  const el = $("employee-draft-preview");
  if (!el) return;
  if (pendingText) {
    el.innerHTML = `<div class="empty">${esc(pendingText)}</div>`;
    return;
  }
  draft = draft || currentDraft();
  if (!draft) {
    el.innerHTML = "Describe the role, then generate a draft.";
    return;
  }
  const recs = state.skillRecommendations.filter((item) => item.draft_id === draft.id);
  el.innerHTML = `
    <div class="draft-title">${esc(draft.display_name || draft.agent_name)}</div>
    <div class="draft-meta">${esc(draft.agent_name)} · ${esc(draft.role || "specialist")}</div>
    <p>${esc(draft.description || draft.requirement || "")}</p>
    <div class="pill-row">${(draft.capabilities || []).map((item) => `<span class="pill">${esc(item)}</span>`).join("")}</div>
    <div class="draft-section">
      <strong>Tools</strong>
      <div>${(draft.tools || []).length ? draft.tools.map((item) => `<span class="pill">${esc(item)}</span>`).join("") : `<span class="muted">Inherit global tools</span>`}</div>
    </div>
    <div class="draft-section">
      <strong>Skill recommendations</strong>
      ${recs.length ? recs.map((rec) => `
        <div class="skill-rec">
          <span>${esc(rec.title || rec.name)} <small>${esc(rec.status || "suggested")}</small></span>
          ${rec.status === "suggested" ? `<button type="button" class="secondary" data-approve-skill="${esc(rec.id)}">Approve</button>` : ""}
        </div>
      `).join("") : `<div class="muted">No recommendations yet.</div>`}
    </div>
  `;
  $("btn-generate-employee").disabled = false;
  $("btn-create-employee").disabled = draft.status !== "draft";
}

function currentDraft() {
  return state.employeeDrafts.find((item) => item.id === state.selectedDraftId);
}

function mention(name) {
  const input = $("composer-input");
  const prefix = input.value.trim() ? " " : "";
  input.value += `${prefix}@${name} `;
  input.focus({ preventScroll: true });
  autoSize(input);
}

function selectedValues(select) {
  return Array.from(select.selectedOptions || []).map((item) => item.value).filter(Boolean);
}

function setSelectedValues(select, values) {
  const selected = new Set((values || []).map(String));
  Array.from(select.options || []).forEach((option) => {
    option.selected = selected.has(option.value);
  });
}

function splitList(value) {
  return String(value || "")
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function teamName(teamId) {
  return state.teams.find((team) => team.id === teamId)?.name || "";
}

function formatTime(ts) {
  if (!ts) return "";
  const date = new Date(Number(ts) * 1000);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatText(text) {
  return esc(text).replace(/\n/g, "<br>");
}

function short(value, len) {
  const text = String(value || "");
  return text.length > len ? `${text.slice(0, len - 1)}...` : text;
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function autoSize(input) {
  input.style.height = "auto";
  input.style.height = `${Math.min(160, input.scrollHeight)}px`;
}

function setStatus(value) {
  const el = $("conn-status");
  el.textContent = value;
  el.className = value;
}

function bindEvents() {
  $$(".rail-item, .tab").forEach((el) => el.addEventListener("click", () => setMode(el.dataset.mode)));
  $("btn-refresh").addEventListener("click", refreshAll);
  $("btn-new-team").addEventListener("click", () => openTeamDialog());
  $("btn-new-goal").addEventListener("click", () => openGoalDialog());
  $("btn-new-employee").addEventListener("click", () => {
    state.selectedDraftId = "";
    renderEmployeeDraftPreview(null);
    $("employee-dialog").showModal();
  });
  $("btn-mention-all").addEventListener("click", () => mention("all"));
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    submitMessage();
  });
  $("composer-input").addEventListener("input", (event) => autoSize(event.currentTarget));
  $("composer-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitMessage();
    }
  });
  $("team-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitTeam(event.currentTarget);
  });
  $("goal-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitGoal(event.currentTarget);
  });
  $("employee-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitEmployeeDraft(event.currentTarget);
  });
  $("employee-edit-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitEmployeeEdit(event.currentTarget);
  });
  $("btn-create-employee").addEventListener("click", createEmployeeFromDraft);
  document.addEventListener("click", (event) => {
    const channel = event.target.closest("[data-channel-id]");
    if (channel) {
      state.selectedChannelId = channel.dataset.channelId;
      loadCurrentHistory();
      render();
      return;
    }
    const mentionBtn = event.target.closest("[data-mention]");
    if (mentionBtn) {
      mention(mentionBtn.dataset.mention);
      return;
    }
    const draftBtn = event.target.closest("[data-open-draft]");
    if (draftBtn) {
      state.selectedDraftId = draftBtn.dataset.openDraft;
      renderEmployeeDraftPreview(currentDraft());
      $("employee-dialog").showModal();
      return;
    }
    const skillBtn = event.target.closest("[data-approve-skill]");
    if (skillBtn) {
      approveSkillRecommendation(skillBtn.dataset.approveSkill);
      return;
    }
    const editTeam = event.target.closest("[data-edit-team]");
    if (editTeam) {
      openTeamDialog(state.teams.find((item) => item.id === editTeam.dataset.editTeam));
      return;
    }
    const archiveTeam = event.target.closest("[data-archive-team]");
    if (archiveTeam) {
      archiveRecord("team", archiveTeam.dataset.archiveTeam);
      return;
    }
    const editGoal = event.target.closest("[data-edit-goal]");
    if (editGoal) {
      openGoalDialog(state.goals.find((item) => item.id === editGoal.dataset.editGoal));
      return;
    }
    const completeGoal = event.target.closest("[data-complete-goal]");
    if (completeGoal) {
      send({ type: "opc_complete_goal", goal_id: completeGoal.dataset.completeGoal });
      return;
    }
    const archiveGoal = event.target.closest("[data-archive-goal]");
    if (archiveGoal) {
      archiveRecord("goal", archiveGoal.dataset.archiveGoal);
      return;
    }
    const editEmployee = event.target.closest("[data-edit-employee]");
    if (editEmployee) {
      openEmployeeEditDialog(state.employees.find((item) => item.id === editEmployee.dataset.editEmployee));
      return;
    }
    const archiveEmployee = event.target.closest("[data-archive-employee]");
    if (archiveEmployee) {
      archiveRecord("employee", archiveEmployee.dataset.archiveEmployee);
      return;
    }
    const deleteDraft = event.target.closest("[data-delete-draft]");
    if (deleteDraft) {
      archiveRecord("draft", deleteDraft.dataset.deleteDraft);
      return;
    }
    const closeBtn = event.target.closest("[data-close-dialog]");
    if (closeBtn) {
      closeDialog(closeBtn);
      return;
    }
    const plan = event.target.closest("[data-plan-goal]");
    if (plan) {
      send({ type: "opc_plan_goal", goal_id: plan.dataset.planGoal });
      return;
    }
    const approve = event.target.closest("[data-approve-goal]");
    if (approve) {
      send({ type: "opc_approve_goal_plan", goal_id: approve.dataset.approveGoal });
      return;
    }
    const goalCard = event.target.closest("[data-goal-id]");
    if (goalCard) {
      state.selectedGoalId = goalCard.dataset.goalId;
      renderContext();
      return;
    }
  });
}

bindEvents();
render();
connect();
