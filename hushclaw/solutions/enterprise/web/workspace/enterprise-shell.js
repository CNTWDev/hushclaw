const state = {
  route: "workbench",
  profile: null,
  overview: null,
  domains: [],
  crmRecords: {},
  crmEvents: [],
  crmNextActions: [],
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

function send(payload) {
  const ws = window.__hc_ws;
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
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
  }).join("");
  el.querySelectorAll("[data-route]").forEach((btn) => {
    btn.addEventListener("click", () => setRoute(btn.dataset.route));
  });
}

function renderWorkbench() {
  const org = state.overview?.directory?.org || {};
  const counts = state.overview?.directory?.counts || {};
  const enabled = state.domains.filter((item) => item.status?.enabled && item.manifest?.module_type !== "foundation");
  const leads = state.crmRecords.lead || [];
  return `
    <section class="enterprise-admin-section">
      <div class="enterprise-admin-metrics">
        ${metric("Members", Number(counts.members || 0))}
        ${metric("Enabled domains", enabled.length)}
        ${metric("CRM leads", leads.length)}
        ${metric("Next actions", state.crmNextActions.length)}
      </div>
      <div class="enterprise-shell-grid">
        ${card(org.name || "Organization", `<span>${esc(state.profile?.distro?.name || "HushClaw Enterprise")}</span><ul><li>${Number(counts.units || 0)} units</li><li>${Number(counts.positions || 0)} positions</li></ul>`)}
        ${card("Business Domains", `<ul>${state.domains.filter((item) => item.manifest?.module_type !== "foundation").map((item) => `<li>${esc(item.manifest?.name || item.manifest?.id)} · ${item.status?.enabled ? "enabled" : item.manifest?.status || "available"}</li>`).join("")}</ul>`)}
        ${card("CRM Focus", `<ul>${leads.slice(0, 5).map((lead) => `<li>${esc(lead.name || lead.id)} · ${esc(lead.status || "new")}</li>`).join("") || "<li>No leads yet</li>"}</ul>`)}
        ${card("Administration", `<span>Manage people, roles, modules, and audit in the enterprise admin console.</span><ul><li><a href="/enterprise/admin">Open admin console</a></li></ul>`)}
      </div>
    </section>
  `;
}

function renderCRM() {
  if (!isDomainEnabled("crm")) {
    return `<section class="enterprise-admin-section">${card("CRM Disabled", `<span>Enable CRM in Enterprise Admin before using the CRM workspace.</span><ul><li><a href="/enterprise/admin#modules">Open module catalog</a></li></ul>`)}</section>`;
  }
  const leads = state.crmRecords.lead || [];
  const accounts = state.crmRecords.account || [];
  const opportunities = state.crmRecords.opportunity || [];
  return `
    <section class="enterprise-admin-section">
      ${card("Create Lead", `
        <form class="enterprise-form enterprise-form-grid" data-action="crm-create-lead">
          <input name="name" placeholder="Lead name" autocomplete="off">
          <input name="source" placeholder="Source" autocomplete="off">
          <input name="owner_id" placeholder="Owner member id" autocomplete="off">
          <input name="team_id" placeholder="Team id" autocomplete="off">
          <button>Create Lead</button>
        </form>
      `)}
      <div class="enterprise-admin-metrics">
        ${metric("Leads", leads.length)}
        ${metric("Accounts", accounts.length)}
        ${metric("Opportunities", opportunities.length)}
        ${metric("Next actions", state.crmNextActions.length)}
      </div>
      <div class="enterprise-admin-two-col">
        ${card("Leads", renderRecordList(leads, "status"))}
        ${card("Next Actions", renderNextActions(state.crmNextActions))}
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

function renderEventList(events) {
  return `<ul>${events.slice(0, 12).map((event) => `<li>${esc(event.event_type)} · ${esc(event.entity_type)}:${esc(event.entity_id)}</li>`).join("") || "<li>No events yet</li>"}</ul>`;
}

function renderNextActions(actions) {
  return `<ul>${actions.slice(0, 12).map((event) => {
    const payload = event.payload || {};
    return `<li>${esc(payload.suggestion || "Review next action")} · ${esc(event.entity_type)}:${esc(event.entity_id)}</li>`;
  }).join("") || "<li>No suggestions yet</li>"}</ul>`;
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
  if (state.route === "agents") return renderPlaceholder("Agents", "CRM lead qualifier and deal coach are declared by the CRM module; default instantiation comes next.");
  if (state.route === "tasks") return renderPlaceholder("Tasks", "Agent-suggested follow-ups and human approvals will attach here.");
  return renderWorkbench();
}

function render() {
  const status = document.getElementById("runtime-status");
  const title = document.getElementById("workspace-title");
  const subtitle = document.getElementById("workspace-subtitle");
  const content = document.getElementById("workspace-content");
  const distro = state.profile?.distro || {};
  if (status) status.textContent = distro.id ? `${esc(distro.id)} runtime` : "Connecting...";
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
}

function handleFormSubmit(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  if (form.dataset.action === "crm-create-lead") {
    send({ type: "crm_create_lead", lead: data });
    form.reset();
  }
}

function refreshCRM() {
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
  refreshCRM();
}

function handleMessage(data) {
  if (data.type === "os_runtime_profile") state.profile = data;
  if (data.type === "enterprise_overview") state.overview = data;
  if (data.type === "os_domains") state.domains = data.items || [];
  if (data.type === "crm_records") state.crmRecords[data.entity_type || ""] = data.items || [];
  if (data.type === "crm_events") state.crmEvents = data.items || [];
  if (data.type === "crm_next_actions") state.crmNextActions = data.items || [];
  if (data.type === "crm_mutation_result") {
    state.crmRecords[data.entity_type || ""] = data.items || [];
    state.crmEvents = data.events || state.crmEvents;
    state.crmNextActions = data.next_actions || state.crmNextActions;
    send({ type: "enterprise_get_overview" });
  }
  render();
}

window.addEventListener("hashchange", () => setRoute(routeFromHash()));

const ws = new WebSocket(wsUrl());
window.__hc_ws = ws;
state.route = routeFromHash();
ws.addEventListener("open", refreshAll);
ws.addEventListener("message", (event) => {
  try { handleMessage(JSON.parse(event.data)); } catch {}
});
ws.addEventListener("close", () => {
  const status = document.getElementById("runtime-status");
  if (status) status.textContent = "Disconnected";
});
render();
