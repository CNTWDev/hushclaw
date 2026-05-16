const state = {
  route: "workbench",
  profile: null,
  overview: null,
  domains: [],
  crmRecords: {},
  crmEvents: [],
  crmNextActions: [],
  agents: [],
  auth: { checked: false, ok: false, member: null, roles: [] },
  error: "",
};

const nav = [
  ["workbench", "Workbench"],
  ["crm", "CRM"],
  ["domains", "Domains"],
  ["knowledge", "Knowledge"],
  ["agents", "Agents"],
  ["tasks", "Tasks"],
];

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams(location.search);
  const key = params.get("api_key") || "";
  return `${proto}//${location.host}${key ? `?api_key=${encodeURIComponent(key)}` : ""}`;
}

function authApiBase() {
  const port = Number(location.port || (location.protocol === "https:" ? 443 : 80));
  const apiPort = Number.isFinite(port) ? port + 1 : port;
  return `${location.protocol}//${location.hostname}:${apiPort}`;
}

async function authRequest(path, options = {}) {
  const response = await fetch(`${authApiBase()}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  let data = {};
  try { data = await response.json(); } catch {}
  if (!response.ok) {
    const error = new Error(data.error || "Authentication failed.");
    error.status = response.status;
    throw error;
  }
  return data;
}

async function checkAuth() {
  try {
    const data = await authRequest("/enterprise/auth/me", { method: "GET" });
    state.auth = { checked: true, ok: true, member: data.member || null, roles: data.roles || [] };
    return true;
  } catch {
    state.auth = { checked: true, ok: false, member: null, roles: [] };
    return false;
  }
}

async function login(loginId, password) {
  const data = await authRequest("/enterprise/auth/login", {
    method: "POST",
    body: JSON.stringify({ login_id: loginId, password }),
  });
  state.auth = { checked: true, ok: true, member: data.member || null, roles: data.roles || [] };
}

async function logout() {
  await authRequest("/enterprise/auth/logout", { method: "POST", body: "{}" }).catch(() => {});
  state.auth = { checked: true, ok: false, member: null, roles: [] };
  window.__hc_ws?.close();
  renderLogin();
}

function send(payload) {
  const ws = window.__hc_ws;
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
}

function renderLogin() {
  const content = document.getElementById("workspace-content");
  const status = document.getElementById("runtime-status");
  const title = document.getElementById("workspace-title");
  const subtitle = document.getElementById("workspace-subtitle");
  const navEl = document.getElementById("enterprise-workspace-nav");
  if (navEl) navEl.innerHTML = "";
  if (status) status.textContent = state.auth.checked ? "Sign in required" : "Checking session...";
  if (title) title.textContent = "Enterprise Sign In";
  if (subtitle) subtitle.textContent = "Use your enterprise account to access enabled domains and agents.";
  if (!content) return;
  content.innerHTML = `
    <section class="enterprise-auth-panel">
      <article class="enterprise-shell-card">
        <strong>Sign in</strong>
        ${state.error ? `<div class="enterprise-notice enterprise-error">${esc(state.error)}</div>` : ""}
        <form class="enterprise-form" data-action="auth-login">
          <input name="login_id" placeholder="Email" autocomplete="username">
          <input name="password" placeholder="Password" type="password" autocomplete="current-password">
          <button>Sign In</button>
        </form>
      </article>
    </section>
  `;
  content.querySelector("form[data-action]")?.addEventListener("submit", (event) => {
    event.preventDefault();
    handleFormSubmit(event.target);
  });
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function routeFromHash() {
  const raw = (location.hash || "#workbench").replace(/^#\/?/, "");
  return raw || "workbench";
}

function setRoute(route) {
  state.route = route || "workbench";
  if (location.hash !== `#${state.route}`) history.replaceState(null, "", `#${state.route}`);
  render();
}

function domainById(id) {
  return state.domains.find((item) => item.manifest?.id === id);
}

function isDomainEnabled(id) {
  return !!domainById(id)?.status?.enabled;
}

function card(title, body, extra = "") {
  return `<article class="enterprise-shell-card"><strong>${esc(title)}</strong>${body}${extra}</article>`;
}

function metric(label, value) {
  return `<div class="enterprise-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
}

function renderNav() {
  const el = document.getElementById("enterprise-workspace-nav");
  if (!el) return;
  el.innerHTML = nav.map(([id, label]) => {
    const disabled = id === "crm" && !isDomainEnabled("crm");
    return `<button data-route="${esc(id)}" class="${state.route === id ? "active" : ""}" ${disabled ? "disabled" : ""}>${esc(label)}${disabled ? "<span>disabled</span>" : ""}</button>`;
  }).join("") + (state.auth.member ? `
    <div class="enterprise-auth-user">
      <span>${esc(state.auth.member.display_name || state.auth.member.email || state.auth.member.id)}</span>
      <button class="secondary compact" data-auth-logout>Logout</button>
    </div>
  ` : "");
  el.querySelectorAll("[data-route]").forEach((btn) => {
    btn.addEventListener("click", () => setRoute(btn.dataset.route));
  });
  el.querySelector("[data-auth-logout]")?.addEventListener("click", logout);
}

function renderWorkbench() {
  const org = state.overview?.directory?.org || {};
  const counts = state.overview?.directory?.counts || {};
  const enabled = state.domains.filter((item) => item.status?.enabled);
  const prospects = state.crmRecords.prospect || [];
  return `
    <section class="enterprise-admin-section">
      <div class="enterprise-admin-metrics">
        ${metric("Members", Number(counts.members || 0))}
        ${metric("Enabled domains", enabled.length)}
        ${metric("Prospects", prospects.length)}
        ${metric("Next actions", state.crmNextActions.length)}
      </div>
      <div class="enterprise-shell-grid">
        ${card(org.name || "Organization", `<span>${esc(state.profile?.distro?.name || "HushClaw Enterprise")}</span><ul><li>${Number(counts.units || 0)} units</li><li>${Number(counts.positions || 0)} positions</li></ul>`)}
        ${card("Business Domains", `<ul>${state.domains.map((item) => `<li>${esc(item.manifest?.name || item.manifest?.id)} · ${item.status?.enabled ? "enabled" : item.manifest?.status || "available"}</li>`).join("")}</ul>`)}
        ${card("Partner Pipeline", `<ul>${prospects.slice(0, 5).map((prospect) => `<li>${esc(prospect.name || prospect.id)} · score ${esc(prospect.fit_score || 0)} · ${esc(prospect.status || "new")}</li>`).join("") || "<li>No prospects yet</li>"}</ul>`)}
        ${card("CRM Domain Agents", renderAgentList(domainAgents("crm").slice(0, 5)))}
        ${card("Administration", `<span>Manage people, roles, modules, and audit in the enterprise admin console.</span><ul><li><a href="/enterprise/admin">Open admin console</a></li></ul>`)}
      </div>
    </section>
  `;
}

function renderCRM() {
  if (!isDomainEnabled("crm")) {
    return `<section class="enterprise-admin-section">${card("CRM Disabled", `<span>Enable CRM in Enterprise Admin before using the CRM workspace.</span><ul><li><a href="/enterprise/admin#modules">Open module catalog</a></li></ul>`)}</section>`;
  }
  const prospects = state.crmRecords.prospect || [];
  const signals = state.crmRecords.market_signal || [];
  const drafts = state.crmRecords.outbound_draft || [];
  const accounts = state.crmRecords.account || [];
  const opportunities = state.crmRecords.opportunity || [];
  return `
    <section class="enterprise-admin-section">
      ${card("Create Prospect", `
        <form class="enterprise-form enterprise-form-grid" data-action="crm-create-prospect">
          <input name="name" placeholder="Partner name" autocomplete="off">
          <input name="website" placeholder="Website" autocomplete="off">
          <input name="industry" placeholder="Industry" autocomplete="off">
          <input name="region" placeholder="Region" autocomplete="off">
          <input name="source" placeholder="Source" autocomplete="off">
          <input name="owner_id" placeholder="Owner member id" autocomplete="off">
          <button>Create Prospect</button>
        </form>
      `)}
      ${card("Record Market Signal", `
        <form class="enterprise-form enterprise-form-grid" data-action="crm-record-signal">
          <input name="title" placeholder="Signal title" autocomplete="off">
          <input name="prospect_id" placeholder="Prospect id" autocomplete="off">
          <input name="signal_type" placeholder="funding, hiring, product, news" autocomplete="off">
          <input name="url" placeholder="Source URL" autocomplete="off">
          <input name="summary" placeholder="Summary" autocomplete="off">
          <button>Record Signal</button>
        </form>
      `)}
      ${card("Create Outbound Draft", `
        <form class="enterprise-form enterprise-form-grid" data-action="crm-create-draft">
          <input name="prospect_id" placeholder="Prospect id" autocomplete="off">
          <input name="subject" placeholder="Subject" autocomplete="off">
          <input name="body" placeholder="Draft body" autocomplete="off">
          <button>Create Draft</button>
        </form>
      `)}
      <div class="enterprise-admin-metrics">
        ${metric("Prospects", prospects.length)}
        ${metric("Signals", signals.length)}
        ${metric("Drafts", drafts.length)}
        ${metric("Accounts", accounts.length)}
        ${metric("Next actions", state.crmNextActions.length)}
      </div>
      <div class="enterprise-admin-two-col">
        ${card("Prospects", renderRecordList(prospects, "fit_score"))}
        ${card("Next Actions", renderNextActions(state.crmNextActions))}
      </div>
      <div class="enterprise-admin-two-col">
        ${card("Market Signals", renderRecordList(signals, "signal_type"))}
        ${card("Outbound Drafts", renderDraftList(drafts))}
      </div>
      <div class="enterprise-admin-two-col">
        ${card("Recent CRM Events", renderEventList(state.crmEvents))}
        ${card("Accounts", renderRecordList(accounts, "owner_id"))}
      </div>
      <div class="enterprise-admin-two-col">
        ${card("Opportunities", renderRecordList(opportunities, "stage"))}
      </div>
    </section>
  `;
}

function renderRecordList(items, metaKey) {
  return `<ul>${items.slice(0, 12).map((item) => `<li>${esc(item.name || item.subject || item.id)} · ${esc(item[metaKey] || "—")}</li>`).join("") || "<li>No records yet</li>"}</ul>`;
}

function renderDraftList(items) {
  return `<ul>${items.slice(0, 12).map((item) => `<li>
    ${esc(item.subject || item.id)} · ${esc(item.status || "draft")}
    ${item.status === "draft" ? `<button class="secondary compact" data-draft="${esc(item.id)}" data-status="approved">Approve</button>
    <button class="secondary compact" data-draft="${esc(item.id)}" data-status="rejected">Reject</button>` : ""}
  </li>`).join("") || "<li>No drafts yet</li>"}</ul>`;
}

function renderEventList(events) {
  return `<ul>${events.slice(0, 12).map((event) => `<li>${esc(event.event_type)} · ${esc(event.entity_type)}:${esc(event.entity_id)}</li>`).join("") || "<li>No events yet</li>"}</ul>`;
}

function renderNextActions(actions) {
  return `<ul>${actions.slice(0, 12).map((event) => {
    const payload = event.payload || {};
    const stateId = event.state_id || payload.state_id || "";
    return `<li>
      ${esc(payload.suggestion || "Review next action")} · ${esc(event.status || "suggested")} · ${esc(event.entity_type)}:${esc(event.entity_id)}
      ${stateId ? `<button class="secondary compact" data-next-action="${esc(stateId)}" data-status="accepted">Accept</button>
      <button class="secondary compact" data-next-action="${esc(stateId)}" data-status="dismissed">Dismiss</button>
      <button class="secondary compact" data-next-action="${esc(stateId)}" data-status="completed">Complete</button>` : ""}
    </li>`;
  }).join("") || "<li>No suggestions yet</li>"}</ul>`;
}

function domainAgents(domainId) {
  return state.agents.filter((agent) => agent.domain_id === domainId && agent.owner_type === "domain");
}

function renderAgentList(items) {
  return `<ul>${items.map((agent) => `<li>${esc(agent.name)} · ${esc(agent.description || agent.role || "domain agent")}</li>`).join("") || "<li>No domain agents enabled</li>"}</ul>`;
}

function renderDomains() {
  return `<section class="enterprise-admin-section"><div class="enterprise-shell-grid">${state.domains.map((item) => card(
    item.manifest?.name || item.manifest?.id,
    `<span>${esc(item.manifest?.description || "")}</span><ul><li>${item.status?.enabled ? "enabled" : item.manifest?.status || "available"}</li><li>${(item.manifest?.tools || []).length} tools</li></ul>`
  )).join("")}</div></section>`;
}

function renderPlaceholder(title, copy) {
  return `<section class="enterprise-admin-section">${card(title, `<span>${copy}</span>`)}</section>`;
}

function renderAgents() {
  const domainOwned = state.agents.filter((agent) => agent.owner_type === "domain");
  const employeeOwned = state.agents.filter((agent) => agent.owner_type !== "domain");
  return `
    <section class="enterprise-admin-section">
      <div class="enterprise-admin-two-col">
        ${card("Domain Agents", renderAgentList(domainOwned))}
        ${card("Employee Assistants", renderAgentList(employeeOwned))}
      </div>
    </section>
  `;
}

function routeTitle() {
  return {
    workbench: "Agent Workbench",
    crm: "CRM Workspace",
    domains: "Business Domains",
    knowledge: "Knowledge",
    agents: "Agents",
    tasks: "Tasks",
  }[state.route] || "Agent Workbench";
}

function routeSubtitle() {
  return {
    workbench: "A role-aware view of enabled domains, recent facts, and governed actions.",
    crm: "Lightweight CRM facts, events, and next actions for Agent workflows.",
    domains: "Enabled enterprise modules available to employees.",
    knowledge: "Shared memory and retrieval are provided by AgentOS.",
    agents: "Domain agents will appear here as modules mature.",
    tasks: "Human review and follow-up tasks will appear here.",
  }[state.route] || "";
}

function renderContent() {
  if (state.route === "crm") return renderCRM();
  if (state.route === "domains") return renderDomains();
  if (state.route === "knowledge") return renderPlaceholder("Knowledge", "Shared memory scopes are available through AgentOS; domain-specific knowledge views will attach here.");
  if (state.route === "agents") return renderAgents();
  if (state.route === "tasks") return renderPlaceholder("Tasks", "Agent-suggested follow-ups and human approvals will attach here.");
  return renderWorkbench();
}

function render() {
  if (state.auth.checked && !state.auth.ok) {
    renderLogin();
    return;
  }
  const status = document.getElementById("runtime-status");
  const title = document.getElementById("workspace-title");
  const subtitle = document.getElementById("workspace-subtitle");
  const content = document.getElementById("workspace-content");
  const distro = state.profile?.distro || {};
  const member = state.auth.member;
  if (status) status.textContent = distro.id ? `${esc(member?.display_name || member?.email || distro.id)} · runtime` : "Connecting...";
  if (title) title.textContent = routeTitle();
  if (subtitle) subtitle.textContent = routeSubtitle();
  renderNav();
  if (content) content.innerHTML = renderContent();
  content?.querySelectorAll("form[data-action]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      handleFormSubmit(form);
    });
  });
  content?.querySelectorAll("[data-next-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      send({
        type: "crm_update_next_action",
        state_id: btn.dataset.nextAction || "",
        status: btn.dataset.status || "",
      });
    });
  });
  content?.querySelectorAll("[data-draft]").forEach((btn) => {
    btn.addEventListener("click", () => {
      send({
        type: "crm_update_outbound_draft",
        draft_id: btn.dataset.draft || "",
        status: btn.dataset.status || "",
      });
    });
  });
}

function handleFormSubmit(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  if (form.dataset.action === "auth-login") {
    login(data.login_id || "", data.password || "")
      .then(() => startWebSocket())
      .catch((error) => {
        state.error = error.message || "Sign in failed.";
        renderLogin();
      });
    return;
  }
  if (form.dataset.action === "crm-create-lead") {
    send({ type: "crm_create_lead", lead: data });
    form.reset();
  }
  if (form.dataset.action === "crm-create-prospect") {
    send({ type: "crm_create_record", entity_type: "prospect", record: data });
    form.reset();
  }
  if (form.dataset.action === "crm-record-signal") {
    send({ type: "crm_create_record", entity_type: "market_signal", record: data });
    form.reset();
  }
  if (form.dataset.action === "crm-create-draft") {
    send({ type: "crm_create_record", entity_type: "outbound_draft", record: data });
    form.reset();
  }
}

function refreshCRM() {
  send({ type: "crm_list_records", entity_type: "prospect", limit: 50 });
  send({ type: "crm_list_records", entity_type: "market_signal", limit: 50 });
  send({ type: "crm_list_records", entity_type: "outbound_draft", limit: 50 });
  send({ type: "crm_list_records", entity_type: "lead", limit: 50 });
  send({ type: "crm_list_records", entity_type: "account", limit: 50 });
  send({ type: "crm_list_records", entity_type: "opportunity", limit: 50 });
  send({ type: "crm_list_events", limit: 50 });
  send({ type: "crm_list_next_actions", limit: 20 });
}

function refreshAll() {
  send({ type: "os_get_runtime_profile" });
  send({ type: "enterprise_get_overview" });
  send({ type: "os_list_domains" });
  send({ type: "list_agents" });
  refreshCRM();
}

function handleMessage(data) {
  if (data.type === "os_runtime_profile") state.profile = data;
  if (data.type === "enterprise_overview") state.overview = data;
  if (data.type === "os_domains") state.domains = data.items || [];
  if (data.type === "crm_records") state.crmRecords[data.entity_type || ""] = data.items || [];
  if (data.type === "crm_events") state.crmEvents = data.items || [];
  if (data.type === "crm_next_actions") state.crmNextActions = data.items || [];
  if (data.type === "agents") state.agents = data.items || [];
  if (data.type === "crm_mutation_result") {
    state.crmRecords[data.entity_type || ""] = data.items || [];
    state.crmEvents = data.events || state.crmEvents;
    state.crmNextActions = data.next_actions || state.crmNextActions;
    send({ type: "enterprise_get_overview" });
  }
  if (data.type === "crm_next_action_result") {
    state.crmNextActions = data.next_actions || state.crmNextActions;
    state.crmEvents = data.events || state.crmEvents;
  }
  if (data.type === "crm_outbound_draft_result") {
    state.crmRecords.outbound_draft = data.items || state.crmRecords.outbound_draft || [];
    state.crmEvents = data.events || state.crmEvents;
  }
  render();
}

window.addEventListener("hashchange", () => setRoute(routeFromHash()));

state.route = routeFromHash();

function startWebSocket() {
  window.__hc_ws?.close();
  const ws = new WebSocket(wsUrl());
  window.__hc_ws = ws;
  ws.addEventListener("open", refreshAll);
  ws.addEventListener("message", (event) => {
    try { handleMessage(JSON.parse(event.data)); } catch {}
  });
  ws.addEventListener("close", () => {
    const status = document.getElementById("runtime-status");
    if (status) status.textContent = state.auth.ok ? "Disconnected" : "Sign in required";
  });
  render();
}

checkAuth().then((ok) => {
  if (ok) startWebSocket();
  else renderLogin();
});
