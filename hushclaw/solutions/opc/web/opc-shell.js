const state = {
  ws: null,
  view: "overview",
  agents: [],
  employees: [],
  teams: [],
  goals: [],
  discussions: [],
  workItems: [],
  selectedGoalId: "",
  selectedTeamId: "",
  activity: [],
};

const $ = (id) => document.getElementById(id);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

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

function setStatus(value) {
  const el = $("conn-status");
  if (!el) return;
  el.textContent = value;
  el.className = `conn-status ${value}`;
}

function connect() {
  setStatus("connecting");
  const ws = new WebSocket(wsUrl());
  state.ws = ws;
  ws.onopen = () => {
    setStatus("connected");
    addActivity("Connected to AgentOS.");
    refreshAll();
  };
  ws.onmessage = (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch { return; }
    handleMessage(data);
  };
  ws.onclose = () => {
    setStatus("disconnected");
    setTimeout(connect, 1600);
  };
  ws.onerror = () => ws.close();
}

function refreshAll() {
  send({ type: "opc_get_overview" });
  send({ type: "list_agents" });
}

function handleMessage(data) {
  switch (data.type) {
    case "agents":
      state.agents = data.items || [];
      render();
      break;
    case "opc_overview":
      state.employees = data.employees || [];
      state.teams = data.teams || [];
      state.goals = data.goals || [];
      state.discussions = data.discussions || [];
      state.workItems = data.work_items || state.workItems || [];
      render();
      break;
    case "opc_teams":
      state.teams = data.items || [];
      render();
      break;
    case "opc_team_saved":
      state.teams = data.items || [];
      state.selectedTeamId = data.item?.id || state.selectedTeamId;
      addActivity(`Team saved: ${data.item?.name || data.item?.id || ""}`);
      refreshAll();
      break;
    case "opc_goal_saved":
      state.goals = data.items || [];
      state.selectedGoalId = data.item?.id || state.selectedGoalId;
      addActivity(`Goal created: ${data.item?.objective || ""}`);
      refreshAll();
      break;
    case "opc_goal_plan":
      state.selectedGoalId = data.goal?.id || state.selectedGoalId;
      state.workItems = data.work_items || [];
      addActivity(`Goal planned: ${data.goal?.objective || ""}`);
      refreshAll();
      break;
    case "opc_goal_approved":
      state.selectedGoalId = data.goal?.id || state.selectedGoalId;
      addActivity(`Goal approved: ${data.todos?.length || 0} todo(s) created.`);
      refreshAll();
      break;
    case "opc_discussion":
      state.selectedTeamId = data.item?.team_id || state.selectedTeamId;
      addActivity(`Discussion completed: ${data.item?.topic || ""}`);
      refreshAll();
      break;
    case "error":
      addActivity(`Error: ${data.message || "Unknown error"}`);
      break;
    default:
      break;
  }
}

function switchView(view) {
  state.view = view;
  $$(".nav-item").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === view));
  $$(".view").forEach((el) => el.classList.toggle("active", el.id === `view-${view}`));
  const titles = {
    overview: ["Overview", "Operate goals through digital employees and persistent teams."],
    employees: ["Employees", "Digital employees synced from AgentOS agents."],
    teams: ["Teams", "Persistent groups for recurring operating work."],
    goals: ["Goals", "Set objectives, plan with teams, approve work."],
    discussions: ["Discussions", "Roundtable records, summaries, and decisions."],
    work: ["Work", "Draft and approved OPC work items."],
  };
  $("view-title").textContent = titles[view][0];
  $("view-subtitle").textContent = titles[view][1];
}

function render() {
  renderMetrics();
  renderSelects();
  renderOverview();
  renderEmployees();
  renderTeams();
  renderGoals();
  renderDiscussions();
  renderWork();
  renderContext();
}

function renderMetrics() {
  $("metric-employees").textContent = state.employees.length;
  $("metric-teams").textContent = state.teams.length;
  $("metric-goals").textContent = state.goals.length;
  $("metric-discussions").textContent = state.discussions.length;
}

function renderSelects() {
  const agentOptions = state.agents.map((agent) => `<option value="${esc(agent.name)}">${esc(agent.name)}</option>`).join("");
  $$("select[name='facilitator']").forEach((el) => { el.innerHTML = `<option value="">Facilitator</option>${agentOptions}`; });
  $$("select[name='member_agents']").forEach((el) => { el.innerHTML = agentOptions; });
  const teamOptions = state.teams.map((team) => `<option value="${esc(team.id)}">${esc(team.name)}</option>`).join("");
  $$("select[name='team_id']").forEach((el) => { el.innerHTML = `<option value="">Team</option>${teamOptions}`; });
  const goalOptions = state.goals.map((goal) => `<option value="${esc(goal.id)}">${esc(short(goal.objective, 48))}</option>`).join("");
  $$("select[name='goal_id']").forEach((el) => { el.innerHTML = `<option value="">No linked goal</option>${goalOptions}`; });
}

function renderOverview() {
  renderList("overview-goals", state.goals.slice(0, 5), renderGoalRow, "No goals yet.");
  renderList("overview-discussions", state.discussions.slice(0, 5), renderDiscussionRow, "No discussions yet.");
}

function renderEmployees() {
  renderList("employees-list", state.employees, (item) => `
    <article class="card">
      <div class="card-title">${esc(item.display_name || item.agent_name)} <span class="chip">${esc(item.role || "specialist")}</span></div>
      <div class="card-meta">${esc(item.description || "No description")}</div>
      <div class="chips">${(item.capabilities || []).map((cap) => `<span class="chip">${esc(cap)}</span>`).join("")}</div>
    </article>
  `, "No employees synced yet.");
}

function renderTeams() {
  renderList("teams-list", state.teams, (team) => `
    <article class="card" data-team-id="${esc(team.id)}">
      <div class="card-title">${esc(team.name)} <span class="chip">${esc(team.facilitator || "no facilitator")}</span></div>
      <div class="card-meta">${esc(team.purpose || "No purpose set")}</div>
      <div class="chips">${(team.member_agents || []).map((name) => `<span class="chip">${esc(name)}</span>`).join("")}</div>
      <button class="secondary" data-discuss-team="${esc(team.id)}">Discuss</button>
    </article>
  `, "No teams yet.");
}

function renderGoals() {
  renderList("goals-list", state.goals, renderGoalRow, "No goals yet.");
}

function renderGoalRow(goal) {
  return `
    <article class="row-card" data-goal-id="${esc(goal.id)}">
      <div class="card-title">
        <span>${esc(goal.objective)}</span>
        <span class="chip">${esc(goal.status || "draft")}</span>
      </div>
      <div class="card-body">${esc(goal.success_criteria || "No success criteria")}</div>
      <div class="chips">
        <span class="chip">priority ${esc(goal.priority || 0)}</span>
        <span class="chip">${esc(teamName(goal.team_id) || "no team")}</span>
      </div>
      <div>
        <button class="secondary" data-plan-goal="${esc(goal.id)}">Plan</button>
        <button class="primary" data-approve-goal="${esc(goal.id)}">Approve Plan</button>
      </div>
    </article>
  `;
}

function renderDiscussions() {
  renderList("discussions-list", state.discussions, renderDiscussionRow, "No discussions yet.");
}

function renderDiscussionRow(item) {
  return `
    <article class="row-card">
      <div class="card-title">
        <span>${esc(item.topic || "Discussion")}</span>
        <span class="chip">${esc(teamName(item.team_id) || "team")}</span>
      </div>
      <div class="card-body">${esc(short(item.summary || "", 260))}</div>
      <div class="chips">${(item.participants || []).map((name) => `<span class="chip">${esc(name)}</span>`).join("")}</div>
    </article>
  `;
}

function renderWork() {
  renderList("work-list", state.workItems, (item) => `
    <article class="row-card">
      <div class="card-title">${esc(item.title || "Work item")} <span class="chip">${esc(item.status || "draft")}</span></div>
      <div class="card-body">${esc(item.notes || "")}</div>
      <div class="chips">
        <span class="chip">${esc(item.assigned_agent || "unassigned")}</span>
        ${item.todo_id ? `<span class="chip">${esc(item.todo_id)}</span>` : ""}
      </div>
    </article>
  `, "No work items in this session. Plan a goal to draft work.");
}

function renderContext() {
  const goal = state.goals.find((item) => item.id === state.selectedGoalId);
  const team = state.teams.find((item) => item.id === (state.selectedTeamId || goal?.team_id));
  $("selected-goal").innerHTML = goal ? `
    <strong>${esc(goal.objective)}</strong><br>
    <span>${esc(goal.status || "draft")} · ${esc(teamName(goal.team_id) || "no team")}</span>
  ` : "No goal selected.";
  $("selected-team").innerHTML = team ? `
    <strong>${esc(team.name)}</strong><br>
    <span>${esc((team.member_agents || []).join(", "))}</span>
  ` : "No team selected.";
  $("activity-log").innerHTML = state.activity.map((item) => `<div>${esc(item)}</div>`).join("") || `<div class="muted">No activity yet.</div>`;
}

function renderList(id, items, renderer, emptyText) {
  const el = $(id);
  if (!el) return;
  el.innerHTML = items.length ? items.map(renderer).join("") : `<div class="empty">${esc(emptyText)}</div>`;
}

function addActivity(text) {
  state.activity.unshift(text);
  state.activity = state.activity.slice(0, 8);
  renderContext();
}

function selectedValues(select) {
  return Array.from(select.selectedOptions || []).map((opt) => opt.value).filter(Boolean);
}

function submitTeam(form) {
  const fd = new FormData(form);
  const multi = form.querySelector("select[name='member_agents']");
  send({
    type: "opc_create_team",
    team: {
      name: fd.get("name"),
      purpose: fd.get("purpose"),
      facilitator: fd.get("facilitator"),
      member_agents: selectedValues(multi),
    },
  });
  form.reset();
}

function submitGoal(form) {
  const fd = new FormData(form);
  send({
    type: "opc_create_goal",
    goal: {
      objective: fd.get("objective"),
      success_criteria: fd.get("success_criteria"),
      team_id: fd.get("team_id"),
      priority: Number(fd.get("priority") || 0),
    },
  });
  form.reset();
}

function submitDiscussion(form) {
  const fd = new FormData(form);
  send({
    type: "opc_start_discussion",
    team_id: fd.get("team_id"),
    goal_id: fd.get("goal_id"),
    topic: fd.get("topic"),
  });
  form.reset();
}

function teamName(teamId) {
  return state.teams.find((team) => team.id === teamId)?.name || "";
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

function bindEvents() {
  $$(".nav-item").forEach((btn) => btn.addEventListener("click", () => switchView(btn.dataset.view)));
  $$("[data-view-jump]").forEach((btn) => btn.addEventListener("click", () => switchView(btn.dataset.viewJump)));
  $("btn-refresh").addEventListener("click", refreshAll);
  $("btn-new-goal").addEventListener("click", () => $("goal-dialog").showModal());
  $("team-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    submitTeam(ev.currentTarget);
  });
  $("goal-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    submitGoal(ev.currentTarget);
  });
  $("dialog-goal-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    submitGoal(ev.currentTarget);
    $("goal-dialog").close();
  });
  $("discussion-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    submitDiscussion(ev.currentTarget);
  });
  document.addEventListener("click", (ev) => {
    const plan = ev.target.closest("[data-plan-goal]");
    if (plan) {
      state.selectedGoalId = plan.dataset.planGoal;
      send({ type: "opc_plan_goal", goal_id: plan.dataset.planGoal });
      renderContext();
      return;
    }
    const approve = ev.target.closest("[data-approve-goal]");
    if (approve) {
      state.selectedGoalId = approve.dataset.approveGoal;
      send({ type: "opc_approve_goal_plan", goal_id: approve.dataset.approveGoal });
      renderContext();
      return;
    }
    const discuss = ev.target.closest("[data-discuss-team]");
    if (discuss) {
      state.selectedTeamId = discuss.dataset.discussTeam;
      switchView("discussions");
      const select = document.querySelector("#discussion-form select[name='team_id']");
      if (select) select.value = state.selectedTeamId;
      renderContext();
      return;
    }
    const goalCard = ev.target.closest("[data-goal-id]");
    if (goalCard) {
      state.selectedGoalId = goalCard.dataset.goalId;
      renderContext();
    }
    const teamCard = ev.target.closest("[data-team-id]");
    if (teamCard) {
      state.selectedTeamId = teamCard.dataset.teamId;
      renderContext();
    }
  });
}

bindEvents();
render();
connect();
