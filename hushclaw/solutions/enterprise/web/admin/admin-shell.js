const state = {
  route: "overview",
  profile: null,
  overview: null,
  domains: [],
  foundation: [],
  units: [],
  positions: [],
  members: [],
  roles: [],
  assignments: [],
  domainAccess: [],
  audit: [],
  settings: null,
  auth: { checked: false, ok: false, member: null, roles: [] },
  domainConfigs: {},
  domainDependencies: {},
  crmRecords: {},
  crmEvents: [],
  auditFilter: "",
  notice: "",
  error: "",
  pendingAction: "",
  pendingType: "",
  modal: null,
};

let noticeTimer = null;

const nav = [
  { group: "Platform", items: [
    ["overview", "Overview"],
    ["organization", "Organization"],
    ["access", "Access"],
    ["modules", "Modules"],
    ["audit", "Audit"],
    ["settings", "Settings"],
  ] },
  { group: "Domains", items: [
    ["domain:crm", "CRM"],
    ["domain:hr", "HR"],
    ["domain:finance", "Finance"],
    ["domain:custom", "Custom Domains"],
  ] },
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

function renderLogin() {
  const content = document.getElementById("admin-content");
  const status = document.getElementById("runtime-status");
  const title = document.getElementById("admin-title");
  const subtitle = document.getElementById("admin-subtitle");
  if (status) status.textContent = state.auth.checked ? "Sign in required" : "Checking session...";
  if (title) title.textContent = "Enterprise Sign In";
  if (subtitle) subtitle.textContent = "Use an enterprise account created by an administrator.";
  const rail = document.getElementById("enterprise-admin-nav");
  if (rail) rail.innerHTML = "";
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
        <p class="enterprise-auth-hint">Bootstrap account: local@hushclaw.enterprise / hushclaw-admin</p>
      </article>
    </section>
  `;
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

function showNotice(message) {
  state.notice = message || "";
  state.error = "";
  if (noticeTimer) clearTimeout(noticeTimer);
  if (state.notice) {
    noticeTimer = setTimeout(() => {
      state.notice = "";
      render();
    }, 4500);
  }
}

function showError(message) {
  state.error = message || "Operation failed.";
  state.notice = "";
}

function responseTypeFor(requestType) {
  if (requestType === "enterprise_update_settings") return "enterprise_settings";
  if (requestType === "enterprise_update_domain_config") return "enterprise_domain_config";
  if (requestType === "enterprise_upsert_org_unit") return "enterprise_directory_result";
  if (requestType === "enterprise_upsert_position") return "enterprise_directory_result";
  if (requestType === "enterprise_upsert_member") return "enterprise_directory_result";
  if (requestType === "enterprise_set_member_password") return "enterprise_directory_result";
  if (requestType === "enterprise_deactivate_member") return "enterprise_directory_result";
  if (requestType === "enterprise_upsert_role") return "enterprise_directory_result";
  if (requestType === "enterprise_assign_role") return "enterprise_directory_result";
  if (requestType === "enterprise_revoke_role") return "enterprise_directory_result";
  if (requestType === "enterprise_grant_domain_access") return "enterprise_domain_access";
  if (requestType === "enterprise_revoke_domain_access") return "enterprise_domain_access";
  if (requestType === "crm_create_record") return "crm_records";
  if (requestType === "os_install_domain") return "os_domain_lifecycle_result";
  if (requestType === "os_enable_domain") return "os_domain_lifecycle_result";
  if (requestType === "os_disable_domain") return "os_domain_lifecycle_result";
  return "";
}

function completePending(type, successMessage = "") {
  const matched = state.pendingType && state.pendingType === type;
  if (matched) {
    state.pendingAction = "";
    state.pendingType = "";
    if (successMessage) showNotice(successMessage);
  }
  return matched;
}

function failPending(type, message) {
  if (!state.pendingType || state.pendingType === type) {
    state.pendingAction = "";
    state.pendingType = "";
    showError(message);
  }
}

function send(payload, label = "") {
  const ws = window.__hc_ws;
  if (ws?.readyState === WebSocket.OPEN) {
    if (label) {
      state.pendingAction = label;
      state.pendingType = responseTypeFor(payload.type);
    }
    state.error = "";
    if (label) render();
    ws.send(JSON.stringify(payload));
  } else {
    showError("Enterprise Admin is disconnected.");
    render();
  }
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function routeFromHash() {
  const raw = (location.hash || "#overview").replace(/^#\/?/, "");
  return raw || "overview";
}

function setRoute(route) {
  state.route = route || "overview";
  if (location.hash !== `#${state.route}`) {
    history.pushState(null, "", `#${state.route}`);
  }
  render();
}

function domainById(id) {
  return state.domains.find((item) => item.manifest?.id === id);
}

function domainStatusText(item) {
  const manifest = item?.manifest || {};
  const status = item?.status || {};
  if (manifest.status === "planned") return "Planned";
  if (status.enabled) return "Enabled";
  if (status.installed) return "Installed";
  return "Available";
}

function businessDomains() {
  return state.domains;
}

function lifecycle(action, domainId) {
  if (!domainId) return;
  const type = action === "install" ? "os_install_domain"
    : action === "enable" ? "os_enable_domain"
    : "os_disable_domain";
  send({ type, domain_id: domainId, scope: "org" }, `${action} module`);
}

function renderShell() {
  const rail = document.getElementById("enterprise-admin-nav");
  if (!rail) return;
  rail.innerHTML = nav.map((group) => `
    <div class="enterprise-nav-group">
      <div class="enterprise-nav-label">${esc(group.group)}</div>
      ${group.items.map(([id, label]) => {
        const domainId = id.startsWith("domain:") ? id.split(":", 2)[1] : "";
        const domain = domainId ? domainById(domainId) : null;
        const planned = domain?.manifest?.status === "planned" || domainId === "custom";
        return `<a href="#${esc(id)}" data-route="${esc(id)}" class="enterprise-nav-link ${state.route === id ? "active" : ""}">
          ${esc(label)}${planned ? `<span>planned</span>` : ""}
        </a>`;
      }).join("")}
    </div>
  `).join("");
  const member = state.auth.member;
  if (member) {
    rail.insertAdjacentHTML("beforeend", `
      <div class="enterprise-auth-user">
        <span>${esc(member.display_name || member.email || member.id)}</span>
        <button class="secondary compact" data-auth-logout>Logout</button>
      </div>
    `);
  }
}

function card(title, body, extra = "") {
  return `<article class="enterprise-shell-card"><strong>${esc(title)}</strong>${body}${extra}</article>`;
}

function actionLink(route, label, extraClass = "") {
  return `<a class="enterprise-action-link ${esc(extraClass)}" href="#${esc(route)}" data-route="${esc(route)}">${esc(label)}</a>`;
}

function metric(label, value) {
  return `<div class="enterprise-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
}

function renderOverview() {
  const counts = state.overview?.directory?.counts || {};
  const domains = state.overview?.domains || {};
  const readiness = [
    ["Organization", counts.members ? "ready" : "needs setup"],
    ["Access", state.assignments.length ? "ready" : "needs setup"],
    ["Audit", "ready"],
    ["Modules", domains.total ? "ready" : "needs setup"],
  ];
  return `
    <section class="enterprise-admin-section">
      <div class="enterprise-admin-metrics">
        ${metric("Members", Number(counts.members || 0))}
        ${metric("Units", Number(counts.units || 0))}
        ${metric("Enabled modules", Number(domains.enabled || 0))}
        ${metric("Recent audit", state.audit.length)}
      </div>
      <div class="enterprise-shell-grid">
        ${card("Organization", `<span>Enterprise foundation: units, positions, members, reporting lines.</span><ul>${readiness.slice(0, 1).map(([k, v]) => `<li>${esc(k)} · ${esc(v)}</li>`).join("")}</ul>`, `<div class="enterprise-card-actions">${actionLink("organization", "Manage Organization")}</div>`)}
        ${card("Access Control", `<span>Roles, scoped assignments, and enterprise permissions.</span><ul><li>Assignments · ${esc(state.assignments.length)}</li><li>Roles · ${esc(state.roles.length)}</li></ul>`, `<div class="enterprise-card-actions">${actionLink("access", "Manage Access")}</div>`)}
        ${card("Modules", `<span>Install and configure AgentOS business domains.</span><ul>${businessDomains().map((d) => `<li>${esc(d.manifest?.name || d.manifest?.id)} · ${esc(domainStatusText(d))}</li>`).join("")}</ul>`, `<div class="enterprise-card-actions">${actionLink("modules", "Open Module Catalog")}</div>`)}
        ${card("CRM Domain", `<span>Lightweight customer facts, events, and AgentOS tools.</span><ul><li>Status · ${esc(domainStatusText(domainById("crm")))}</li></ul>`, `<div class="enterprise-card-actions">${actionLink("domain:crm", "Configure CRM")}</div>`)}
        ${card("Audit", renderAuditList(state.audit.slice(0, 5)), `<div class="enterprise-card-actions">${actionLink("audit", "Review Audit")}</div>`)}
        ${card("Settings", `<span>Enterprise defaults for model policy, retention, module install policy, and memory scopes.</span>`, `<div class="enterprise-card-actions">${actionLink("settings", "Open Settings")}</div>`)}
      </div>
    </section>
  `;
}

function renderOrganization() {
  const unitOptions = state.units.map((u) => `<option value="${esc(u.id)}">${esc(u.name)}</option>`).join("");
  const positionOptions = state.positions.map((p) => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("");
  const memberOptions = state.members.map((m) => `<option value="${esc(m.id)}">${esc(m.display_name || m.id)}</option>`).join("");
  return `
    <section class="enterprise-admin-section">
      <div class="enterprise-admin-two-col">
        ${card("Add Unit", `
          <form class="enterprise-form" data-action="create-unit">
            <input name="name" placeholder="Department name" autocomplete="off">
            <button>Create Unit</button>
          </form>
        `)}
        ${card("Add Position", `
          <form class="enterprise-form" data-action="create-position">
            <input name="title" placeholder="Position title" autocomplete="off">
            <select name="unit_id"><option value="">No unit</option>${unitOptions}</select>
            <button>Create Position</button>
          </form>
        `)}
      </div>
      ${card("Add Member", `
        <form class="enterprise-form enterprise-form-grid" data-action="create-member">
          <input name="display_name" placeholder="Display name" autocomplete="off">
          <input name="email" placeholder="Email" autocomplete="off">
          <input name="temporary_password" placeholder="Temporary password" type="password" autocomplete="new-password">
          <select name="unit_id"><option value="">No unit</option>${unitOptions}</select>
          <select name="position_id"><option value="">No position</option>${positionOptions}</select>
          <select name="manager_id"><option value="">No manager</option>${memberOptions}</select>
          <button>Create Member</button>
        </form>
      `)}
      <div class="enterprise-admin-two-col">
        ${card("Organization Units", `<ul>${state.units.map((u) => `<li>${esc(u.name)} · ${esc(u.kind || "department")} <button class="secondary compact" data-edit-unit="${esc(u.id)}">Edit</button></li>`).join("")}</ul>`)}
        ${card("Positions", `<ul>${state.positions.map((p) => `<li>${esc(p.title)} · ${esc(p.status || "active")} <button class="secondary compact" data-edit-position="${esc(p.id)}">Edit</button></li>`).join("")}</ul>`)}
      </div>
      ${card("Members", `
        <table class="enterprise-table">
          <thead><tr><th>Name</th><th>Email</th><th>Title</th><th>Manager</th><th>Status</th><th></th></tr></thead>
          <tbody>${state.members.map((m) => `<tr><td>${esc(m.display_name || m.id)}</td><td>${esc(m.email)}</td><td>${esc(m.title)}</td><td>${esc(m.manager_id || "-")}</td><td>${esc(m.status)}</td><td><button class="secondary compact" data-edit-member="${esc(m.id)}">Edit</button> <button class="secondary compact" data-reset-password="${esc(m.id)}">Set Password</button> <button class="secondary compact" data-deactivate-member="${esc(m.id)}">Deactivate</button></td></tr>`).join("")}</tbody>
        </table>
      `)}
    </section>
  `;
}

function renderAccess() {
  const memberOptions = state.members.map((m) => `<option value="${esc(m.id)}">${esc(m.display_name || m.id)}</option>`).join("");
  const roleOptions = state.roles.map((r) => `<option value="${esc(r.id)}">${esc(r.name || r.id)}</option>`).join("");
  const teamOptions = state.overview?.directory ? "" : "";
  const domainOptions = state.domains.map((d) => `<option value="${esc(d.manifest?.id || "")}">${esc(d.manifest?.name || d.manifest?.id)}</option>`).join("");
  return `
    <section class="enterprise-admin-section">
      <div class="enterprise-admin-two-col">
        ${card("Add Role", `
          <form class="enterprise-form" data-action="create-role">
            <input name="name" placeholder="Role name" autocomplete="off">
            <input name="permissions" placeholder="permission.one, permission.two" autocomplete="off">
            <button>Create Role</button>
          </form>
        `)}
        ${card("Assign Role", `
          <form class="enterprise-form" data-action="assign-role">
            <select name="member_id">${memberOptions}</select>
            <select name="role_id">${roleOptions}</select>
            <select name="scope">
              <option value="org">org</option>
              <option value="domain">domain</option>
            </select>
            <input name="scope_id" placeholder="scope id, e.g. crm" autocomplete="off">
            <button>Assign Role</button>
          </form>
        `)}
        ${card("Grant Domain Access", `
          <form class="enterprise-form" data-action="grant-domain-access">
            <select name="domain_id">${domainOptions}</select>
            <select name="subject_type">
              <option value="member">member</option>
              <option value="team">team</option>
              <option value="role">role</option>
            </select>
            <input name="subject_id" placeholder="member/team/role id" autocomplete="off">
            <select name="access_level">
              <option value="use">use</option>
              <option value="admin">admin</option>
            </select>
            <button>Grant Access</button>
          </form>
        `)}
      </div>
      <div class="enterprise-admin-two-col">
        ${card("Roles", `<ul>${state.roles.map((r) => `<li>${esc(r.name)} · ${(r.permissions || []).length} permissions <button class="secondary compact" data-edit-role="${esc(r.id)}">Edit</button></li>`).join("")}</ul>`)}
        ${card("Assignments", `<ul>${state.assignments.map((a) => `<li>${esc(a.member_id)} → ${esc(a.role_id)} · ${esc(a.scope)}:${esc(a.scope_id)} <button class="secondary compact" data-revoke-role="${esc(a.member_id)}" data-role-id="${esc(a.role_id)}" data-scope="${esc(a.scope)}" data-scope-id="${esc(a.scope_id)}">Revoke</button></li>`).join("")}</ul>`)}
      </div>
      ${card("Domain Access", `<ul>${state.domainAccess.map((a) => `<li>${esc(a.domain_id)} · ${esc(a.subject_type)}:${esc(a.subject_id)} · ${esc(a.access_level)} <button class="secondary compact" data-revoke-domain-access="${esc(a.domain_id)}" data-subject-type="${esc(a.subject_type)}" data-subject-id="${esc(a.subject_id)}">Revoke</button></li>`).join("") || "<li>No domain access grants</li>"}</ul>`)}
    </section>
  `;
}

function optionList(items, selected, labelKey = "name", emptyLabel = "") {
  const empty = emptyLabel ? `<option value="">${esc(emptyLabel)}</option>` : "";
  return empty + items.map((item) => {
    const value = item.id || "";
    const label = item[labelKey] || item.name || item.display_name || item.id;
    return `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`;
  }).join("");
}

function renderModalField(field, value) {
  if (field.type === "select") {
    return `<label><span>${esc(field.label)}</span><select name="${esc(field.name)}">${field.options(value)}</select></label>`;
  }
  if (field.type === "textarea") {
    return `<label><span>${esc(field.label)}</span><textarea name="${esc(field.name)}" rows="${field.rows || 4}">${esc(value)}</textarea></label>`;
  }
  return `<label><span>${esc(field.label)}</span><input name="${esc(field.name)}" value="${esc(value)}" autocomplete="off"></label>`;
}

function modalConfig() {
  const modal = state.modal || {};
  if (modal.kind === "unit") {
    const item = state.units.find((u) => u.id === modal.id) || {};
    return {
      title: "Edit Unit",
      submit: "Save Unit",
      item,
      fields: [
        { name: "name", label: "Name" },
        { name: "kind", label: "Kind" },
        { name: "parent_id", label: "Parent Unit", type: "select", options: (v) => optionList(state.units.filter((u) => u.id !== item.id), v, "name", "No parent") },
        { name: "status", label: "Status", type: "select", options: (v) => optionList([{ id: "active", name: "active" }, { id: "inactive", name: "inactive" }], v) },
      ],
      payload: (data) => ({ type: "enterprise_upsert_org_unit", unit: { ...item, ...data } }),
    };
  }
  if (modal.kind === "position") {
    const item = state.positions.find((p) => p.id === modal.id) || {};
    return {
      title: "Edit Position",
      submit: "Save Position",
      item,
      fields: [
        { name: "title", label: "Title" },
        { name: "unit_id", label: "Unit", type: "select", options: (v) => optionList(state.units, v, "name", "No unit") },
        { name: "status", label: "Status", type: "select", options: (v) => optionList([{ id: "active", name: "active" }, { id: "inactive", name: "inactive" }], v) },
      ],
      payload: (data) => ({ type: "enterprise_upsert_position", position: { ...item, ...data } }),
    };
  }
  if (modal.kind === "member") {
    const item = state.members.find((m) => m.id === modal.id) || {};
    return {
      title: "Edit Member",
      submit: "Save Member",
      item,
      fields: [
        { name: "display_name", label: "Display Name" },
        { name: "email", label: "Email" },
        { name: "unit_id", label: "Unit", type: "select", options: (v) => optionList(state.units, v, "name", "No unit") },
        { name: "position_id", label: "Position", type: "select", options: (v) => optionList(state.positions, v, "title", "No position") },
        { name: "manager_id", label: "Manager", type: "select", options: (v) => optionList(state.members.filter((m) => m.id !== item.id), v, "display_name", "No manager") },
        { name: "title", label: "Title" },
        { name: "status", label: "Status", type: "select", options: (v) => optionList([{ id: "active", name: "active" }, { id: "inactive", name: "inactive" }], v) },
        { name: "identity_provider", label: "Identity Provider" },
        { name: "identity_ref", label: "Identity Ref" },
      ],
      payload: (data) => ({ type: "enterprise_upsert_member", member: { ...item, ...data } }),
    };
  }
  if (modal.kind === "role") {
    const item = state.roles.find((r) => r.id === modal.id) || {};
    return {
      title: "Edit Role",
      submit: "Save Role",
      item: { ...item, permissions: (item.permissions || []).join(", ") },
      fields: [
        { name: "name", label: "Name" },
        { name: "description", label: "Description" },
        { name: "permissions", label: "Permissions", type: "textarea", rows: 5 },
      ],
      payload: (data) => ({
        type: "enterprise_upsert_role",
        role: {
          ...item,
          name: data.name || item.name,
          description: data.description || "",
          permissions: String(data.permissions || "").split(",").map((p) => p.trim()).filter(Boolean),
        },
      }),
    };
  }
  if (modal.kind === "password") {
    const item = state.members.find((m) => m.id === modal.id) || {};
    return {
      title: "Set Password",
      submit: "Save Password",
      item: { password: "", temporary: "true", display_name: item.display_name || item.id },
      fields: [
        { name: "password", label: "Temporary Password" },
        { name: "temporary", label: "Password Status", type: "select", options: (v) => optionList([{ id: "true", name: "temporary" }, { id: "false", name: "active" }], v) },
      ],
      payload: (data) => ({
        type: "enterprise_set_member_password",
        member_id: item.id || modal.id,
        password: data.password || "",
        temporary: data.temporary !== "false",
      }),
    };
  }
  return null;
}

function renderModal() {
  const modal = state.modal;
  if (!modal) return "";
  if (modal.kind === "confirm") {
    return `
      <div class="enterprise-modal-backdrop" role="presentation">
        <section class="enterprise-modal" role="dialog" aria-modal="true">
          <h2>${esc(modal.title || "Confirm Action")}</h2>
          <p>${esc(modal.message || "This action will be applied immediately.")}</p>
          <div class="enterprise-modal-actions">
            <button class="secondary" data-modal-cancel>Cancel</button>
            <button data-modal-confirm>${esc(modal.submit || "Confirm")}</button>
          </div>
        </section>
      </div>
    `;
  }
  const config = modalConfig();
  if (!config) return "";
  return `
    <div class="enterprise-modal-backdrop" role="presentation">
      <section class="enterprise-modal" role="dialog" aria-modal="true">
        <h2>${esc(config.title)}</h2>
        <form class="enterprise-form" data-action="modal-submit">
          ${config.fields.map((field) => renderModalField(field, config.item[field.name] || "")).join("")}
          <div class="enterprise-modal-actions">
            <button class="secondary" type="button" data-modal-cancel>Cancel</button>
            <button>${esc(config.submit)}</button>
          </div>
        </form>
      </section>
    </div>
  `;
}

function renderModules() {
  return `
    <section class="enterprise-admin-section">
      <div class="enterprise-shell-grid">
        ${card("Enterprise Foundation", `<span>Platform substrate for organization, identity, access, audit, and module lifecycle. It is not an installable business domain.</span><ul>${state.foundation.map((item) => `<li>${esc(item.name || item.id)} · ${esc(item.status || "enabled")}</li>`).join("")}</ul>`, `<div class="enterprise-card-actions">${actionLink("organization", "Manage Organization")}${actionLink("access", "Manage Access", "secondary")}</div>`)}
        ${businessDomains().map((item) => renderDomainCard(item)).join("")}
      </div>
    </section>
  `;
}

function renderDomainCard(item) {
  const manifest = item.manifest || {};
  const status = item.status || {};
  const deps = state.domainDependencies[manifest.id] || {};
  const planned = manifest.status === "planned";
  const blocked = Array.isArray(deps.missing) && deps.missing.length > 0;
  const action = status.enabled ? "disable" : status.installed ? "enable" : "install";
  const actionLabel = action[0].toUpperCase() + action.slice(1);
  return `
    <article class="enterprise-shell-card enterprise-domain-card">
      <strong>${esc(manifest.name || manifest.id)}</strong>
      <span>${esc(manifest.description || "")}</span>
      <ul>
        <li>Status · ${esc(domainStatusText(item))}</li>
        <li>Domain deps · ${(manifest.dependencies || []).join(", ") || "none"}</li>
        <li>Platform · ${(manifest.platform_requirements || []).join(", ") || "none"}</li>
        ${blocked ? `<li>Missing · ${esc(deps.missing.join(", "))}</li>` : ""}
        <li>${(manifest.tools || []).length} tools · ${(manifest.agents || []).length} agents</li>
        <li>Permissions · ${(manifest.required_permissions || []).join(", ") || "none"}</li>
      </ul>
      <div class="enterprise-card-actions">
        ${actionLink(`domain:${manifest.id}`, "Configure")}
        <button class="secondary" data-domain-action="${action}" data-domain-id="${esc(manifest.id)}" ${(planned || blocked) ? "disabled" : ""}>${esc(actionLabel)}</button>
      </div>
    </article>
  `;
}

function renderAuditList(items) {
  return `<ul>${items.map((e) => {
    const payload = e.payload || {};
    const principal = payload.principal || {};
    return `<li>${esc(payload.event_type || e.type)} · ${esc(principal.principal_id || "system")}</li>`;
  }).join("") || "<li>No audit events</li>"}</ul>`;
}

function renderAudit() {
  const filtered = state.audit.filter((item) => {
    const q = state.auditFilter.toLowerCase();
    if (!q) return true;
    return JSON.stringify(item).toLowerCase().includes(q);
  });
  return `<section class="enterprise-admin-section">${card("Audit Events", `
    <form class="enterprise-form" data-action="filter-audit">
      <input name="query" value="${esc(state.auditFilter)}" placeholder="Filter audit events" autocomplete="off">
      <button>Filter</button>
    </form>
    ${renderAuditList(filtered)}
  `)}</section>`;
}

function renderSettings() {
  const s = state.settings || {};
  return `
    <section class="enterprise-admin-section">
      ${card("Enterprise Settings", `
        <form class="enterprise-form enterprise-form-grid" data-action="save-settings">
          <input name="org_name" value="${esc(s.org_name || "")}" placeholder="Organization name" autocomplete="off">
          <input name="default_model_policy" value="${esc(s.default_model_policy || "kernel_default")}" placeholder="Model policy" autocomplete="off">
          <input name="audit_retention_days" value="${esc(s.audit_retention_days || 180)}" placeholder="Audit retention days" autocomplete="off">
          <input name="memory_scopes" value="${esc((s.memory_scopes || []).join(", "))}" placeholder="org, domain, workspace" autocomplete="off">
          <select name="module_install_policy">
            <option value="owner_only" ${(s.module_install_policy || "owner_only") === "owner_only" ? "selected" : ""}>owner_only</option>
            <option value="admin_allowed" ${s.module_install_policy === "admin_allowed" ? "selected" : ""}>admin_allowed</option>
          </select>
          <button>Save Settings</button>
        </form>
      `)}
    </section>
  `;
}

function renderDomainPage(domainId) {
  if (domainId === "custom") {
    return `<section class="enterprise-admin-section">${card("Custom Domains", "<span>Third-party and custom domain installation will attach here.</span>", `<div class="enterprise-card-actions">${actionLink("modules", "Back to Modules")}</div>`)}</section>`;
  }
  const item = domainById(domainId);
  if (!item) {
    return `<section class="enterprise-admin-section">${card(
      `${domainId.toUpperCase()} Domain`,
      "<span>This domain is not installed in the current enterprise runtime yet.</span>",
      `<div class="enterprise-card-actions">${actionLink("modules", "Back to Modules")}</div>`,
    )}</section>`;
  }
  const manifest = item.manifest || {};
  const config = state.domainConfigs[domainId]?.config || {};
  const deps = state.domainDependencies[domainId] || {};
  const crmLeadPreview = domainId === "crm"
    ? card("Prospects", `<ul>${(state.crmRecords.prospect || []).slice(0, 8).map((prospect) => `<li>${esc(prospect.name || prospect.id)} · score ${esc(prospect.fit_score || 0)} · ${esc(prospect.industry || "unknown")}</li>`).join("") || "<li>No prospects yet</li>"}</ul>`)
    : "";
  const crmEvents = domainId === "crm"
    ? card("CRM Events", `<ul>${state.crmEvents.slice(0, 8).map((event) => `<li>${esc(event.event_type)} · ${esc(event.entity_type)}:${esc(event.entity_id)}</li>`).join("") || "<li>No CRM events yet</li>"}</ul>`)
    : "";
  const crmSignals = domainId === "crm"
    ? card("Market Signals", `<ul>${(state.crmRecords.market_signal || []).slice(0, 8).map((signal) => `<li>${esc(signal.title || signal.id)} · ${esc(signal.signal_type || "market")} · ${esc(signal.confidence || 0)}</li>`).join("") || "<li>No market signals yet</li>"}</ul>`)
    : "";
  const crmDrafts = domainId === "crm"
    ? card("Outbound Drafts", `<ul>${(state.crmRecords.outbound_draft || []).slice(0, 8).map((draft) => `<li>${esc(draft.subject || draft.id)} · ${esc(draft.status || "draft")}</li>`).join("") || "<li>No outbound drafts yet</li>"}</ul>`)
    : "";
  return `
    <section class="enterprise-admin-section">
      <div class="enterprise-domain-header">
        <div>
          <h2>${esc(manifest.name || domainId)}</h2>
          <p>${esc(manifest.description || "")}</p>
        </div>
        ${actionLink("modules", "Back to Modules", "secondary")}
      </div>
      <div class="enterprise-admin-two-col">
        ${renderDomainCard(item)}
        ${card(domainId === "crm" ? "CRM Strategy Console" : "Configuration", `
          ${domainId === "crm" ? renderCRMStrategyForm(config, domainId) : `
            <form class="enterprise-form" data-action="save-domain-config" data-domain-id="${esc(domainId)}">
              <input name="default_pipeline" value="${esc(config.default_pipeline || "")}" placeholder="Default pipeline" autocomplete="off">
              <input name="lead_sources" value="${esc(Array.isArray(config.lead_sources) ? config.lead_sources.join(", ") : "")}" placeholder="Lead sources, comma-separated" autocomplete="off">
              <button>Save Domain Config</button>
            </form>
          `}
          <dl class="enterprise-detail-list">
            <dt>Scope</dt><dd>${esc(item.status?.metadata?.scope || "org")}</dd>
            <dt>Domain dependencies</dt><dd>${esc((manifest.dependencies || []).join(", ") || "none")}</dd>
            <dt>Platform requirements</dt><dd>${esc((manifest.platform_requirements || []).join(", ") || "none")}</dd>
            <dt>Missing dependencies</dt><dd>${esc((deps.missing || []).join(", ") || "none")}</dd>
            <dt>Configured</dt><dd>${esc(item.status?.configured ? "yes" : "no")}</dd>
            <dt>Config keys</dt><dd>${esc(Object.keys(config).join(", ") || "none")}</dd>
          </dl>
        `)}
      </div>
      ${domainId === "crm" ? `<div class="enterprise-admin-two-col">${crmLeadPreview}${crmSignals}${crmDrafts}${crmEvents}</div>` : ""}
    </section>
  `;
}

function renderCRMStrategyForm(config, domainId) {
  const targetMarkets = config.target_markets || {};
  const partnerProfile = config.partner_profile || {};
  const automation = config.automation || {};
  const governance = config.governance || {};
  return `
    <form class="enterprise-form enterprise-form-grid" data-action="save-domain-config" data-domain-id="${esc(domainId)}">
      <input name="industries" value="${esc((targetMarkets.industries || []).join(", "))}" placeholder="Target industries" autocomplete="off">
      <input name="regions" value="${esc((targetMarkets.regions || []).join(", "))}" placeholder="Target regions" autocomplete="off">
      <input name="keywords" value="${esc((targetMarkets.keywords || []).join(", "))}" placeholder="Discovery keywords" autocomplete="off">
      <input name="excluded_keywords" value="${esc((targetMarkets.excluded_keywords || []).join(", "))}" placeholder="Excluded keywords" autocomplete="off">
      <input name="strong_signals" value="${esc((partnerProfile.strong_signals || []).join(", "))}" placeholder="Strong partner signals" autocomplete="off">
      <input name="weak_signals" value="${esc((partnerProfile.weak_signals || []).join(", "))}" placeholder="Weak partner signals" autocomplete="off">
      <input name="negative_signals" value="${esc((partnerProfile.negative_signals || []).join(", "))}" placeholder="Negative signals" autocomplete="off">
      <input name="daily_scan_time" value="${esc(automation.daily_scan_time || "09:00")}" placeholder="Daily scan time" autocomplete="off">
      <input name="max_daily_prospects" value="${esc(automation.max_daily_prospects || 10)}" placeholder="Max daily prospects" autocomplete="off">
      <select name="auto_create_prospects">
        <option value="true" ${governance.auto_create_prospects !== false ? "selected" : ""}>auto_create_prospects</option>
        <option value="false" ${governance.auto_create_prospects === false ? "selected" : ""}>manual_prospect_review</option>
      </select>
      <select name="outbound_requires_approval">
        <option value="true" ${governance.outbound_requires_approval !== false ? "selected" : ""}>outbound_requires_approval</option>
        <option value="false" ${governance.outbound_requires_approval === false ? "selected" : ""}>allow_unapproved_outbound</option>
      </select>
      <button>Save CRM Strategy</button>
    </form>
  `;
}

function routeTitle() {
  if (state.route.startsWith("domain:")) {
    const id = state.route.split(":", 2)[1];
    const item = domainById(id);
    return item?.manifest?.name || "Domain";
  }
  return {
    overview: "Enterprise Overview",
    organization: "Organization",
    access: "Access Control",
    modules: "Module Catalog",
    audit: "Audit",
    settings: "Settings",
  }[state.route] || "Enterprise Admin";
}

function routeSubtitle() {
  if (state.route.startsWith("domain:")) return "Domain module configuration and lifecycle.";
  return {
    overview: "Operate the enterprise foundation and AI module lifecycle.",
    organization: "Manage organization facts used by principals, policy, and AI workflows.",
    access: "Manage roles, permissions, and scoped assignments.",
    modules: "Install and enable business domains without changing the AgentOS kernel.",
    audit: "Review enterprise administration and module lifecycle events.",
    settings: "Enterprise defaults for model policy, retention, modules, and memory scopes.",
  }[state.route] || "";
}

function renderContent() {
  if (state.route.startsWith("domain:")) return renderDomainPage(state.route.split(":", 2)[1]);
  if (state.route === "organization") return renderOrganization();
  if (state.route === "access") return renderAccess();
  if (state.route === "modules") return renderModules();
  if (state.route === "audit") return renderAudit();
  if (state.route === "settings") return renderSettings();
  return renderOverview();
}

function render() {
  if (state.auth.checked && !state.auth.ok) {
    renderLogin();
    return;
  }
  const status = document.getElementById("runtime-status");
  const title = document.getElementById("admin-title");
  const subtitle = document.getElementById("admin-subtitle");
  const content = document.getElementById("admin-content");
  const distro = state.profile?.distro || {};
  const member = state.auth.member;
  if (status) status.textContent = distro.id ? `${esc(member?.display_name || member?.email || distro.id)} · admin` : "Connecting...";
  if (title) title.textContent = routeTitle();
  if (subtitle) subtitle.textContent = routeSubtitle();
  renderShell();
  if (content) {
    content.innerHTML = `
      ${state.pendingAction ? `<div class="enterprise-notice">Working: ${esc(state.pendingAction)}…</div>` : ""}
      ${state.error ? `<div class="enterprise-notice enterprise-error">${esc(state.error)}</div>` : ""}
      ${state.notice ? `<div class="enterprise-notice">${esc(state.notice)}</div>` : ""}
      ${renderContent()}
      ${renderModal()}
    `;
  }
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function handleFormSubmit(form) {
  const action = form.dataset.action;
  const data = formData(form);
  if (action === "auth-login") {
    login(data.login_id || "", data.password || "")
      .then(() => startWebSocket())
      .catch((error) => {
        showError(error.message || "Sign in failed.");
        renderLogin();
      });
    return;
  }
  if (action === "create-unit") {
    send({ type: "enterprise_upsert_org_unit", unit: { name: data.name || "Untitled Unit" } }, "create unit");
  }
  if (action === "create-position") {
    send({ type: "enterprise_upsert_position", position: { title: data.title || "Untitled Position", unit_id: data.unit_id || "" } }, "create position");
  }
  if (action === "create-member") {
    send({ type: "enterprise_upsert_member", member: data }, "create member");
  }
  if (action === "create-role") {
    send({
      type: "enterprise_upsert_role",
      role: {
        name: data.name || "Untitled Role",
        permissions: String(data.permissions || "").split(",").map((item) => item.trim()).filter(Boolean),
      },
    }, "create role");
  }
  if (action === "assign-role") {
    send({
      type: "enterprise_assign_role",
      member_id: data.member_id || "",
      role_id: data.role_id || "",
      scope: data.scope || "org",
      scope_id: data.scope_id || "",
    }, "assign role");
  }
  if (action === "grant-domain-access") {
    send({
      type: "enterprise_grant_domain_access",
      domain_id: data.domain_id || "",
      subject_type: data.subject_type || "member",
      subject_id: data.subject_id || "",
      access_level: data.access_level || "use",
    }, "grant domain access");
  }
  if (action === "filter-audit") {
    state.auditFilter = data.query || "";
    render();
    return;
  }
  if (action === "save-settings") {
    send({
      type: "enterprise_update_settings",
      settings: {
        org_name: data.org_name || "",
        default_model_policy: data.default_model_policy || "kernel_default",
        audit_retention_days: Number(data.audit_retention_days || 180),
        memory_scopes: String(data.memory_scopes || "").split(",").map((item) => item.trim()).filter(Boolean),
        module_install_policy: data.module_install_policy || "owner_only",
      },
    }, "save settings");
  }
  if (action === "save-domain-config") {
    const domainId = form.dataset.domainId || "";
    const config = domainId === "crm" ? {
      target_markets: {
        industries: csv(data.industries),
        regions: csv(data.regions),
        keywords: csv(data.keywords),
        excluded_keywords: csv(data.excluded_keywords),
      },
      partner_profile: {
        strong_signals: csv(data.strong_signals),
        weak_signals: csv(data.weak_signals),
        negative_signals: csv(data.negative_signals),
      },
      automation: {
        daily_scan_time: data.daily_scan_time || "09:00",
        max_daily_prospects: Number(data.max_daily_prospects || 10),
      },
      governance: {
        auto_create_prospects: data.auto_create_prospects !== "false",
        outbound_requires_approval: data.outbound_requires_approval !== "false",
      },
    } : {
      default_pipeline: data.default_pipeline || "",
      lead_sources: csv(data.lead_sources),
    };
    send({
      type: "enterprise_update_domain_config",
      domain_id: domainId,
      config,
    }, "save domain config");
  }
  form.reset();
}

function csv(value) {
  return String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
}

function handleDocumentClick(event) {
  const target = event.target.closest("button, a");
  if (!target) return;
  const routeTarget = target.closest("[data-route]");
  if (routeTarget) {
    event.preventDefault();
    setRoute(routeTarget.dataset.route);
    return;
  }
  const domainAction = target.closest("[data-domain-action]");
  if (domainAction) {
    event.preventDefault();
    lifecycle(domainAction.dataset.domainAction, domainAction.dataset.domainId);
    return;
  }
  const editUnit = target.closest("[data-edit-unit]");
  if (editUnit) {
    event.preventDefault();
    state.modal = { kind: "unit", id: editUnit.dataset.editUnit };
    render();
    return;
  }
  const editPosition = target.closest("[data-edit-position]");
  if (editPosition) {
    event.preventDefault();
    state.modal = { kind: "position", id: editPosition.dataset.editPosition };
    render();
    return;
  }
  const editMember = target.closest("[data-edit-member]");
  if (editMember) {
    event.preventDefault();
    state.modal = { kind: "member", id: editMember.dataset.editMember };
    render();
    return;
  }
  const resetPassword = target.closest("[data-reset-password]");
  if (resetPassword) {
    event.preventDefault();
    state.modal = { kind: "password", id: resetPassword.dataset.resetPassword };
    render();
    return;
  }
  if (target.closest("[data-auth-logout]")) {
    event.preventDefault();
    logout();
    return;
  }
  const deactivateMember = target.closest("[data-deactivate-member]");
  if (deactivateMember) {
    event.preventDefault();
    const item = state.members.find((m) => m.id === deactivateMember.dataset.deactivateMember);
    state.modal = {
      kind: "confirm",
      title: "Deactivate Member",
      message: `Deactivate ${item?.display_name || deactivateMember.dataset.deactivateMember}?`,
      submit: "Deactivate",
      payload: { type: "enterprise_deactivate_member", member_id: deactivateMember.dataset.deactivateMember || "" },
      label: "deactivate member",
    };
    render();
    return;
  }
  const editRole = target.closest("[data-edit-role]");
  if (editRole) {
    event.preventDefault();
    state.modal = { kind: "role", id: editRole.dataset.editRole };
    render();
    return;
  }
  const revokeRole = target.closest("[data-revoke-role]");
  if (revokeRole) {
    event.preventDefault();
    state.modal = {
      kind: "confirm",
      title: "Revoke Role",
      message: `Revoke ${revokeRole.dataset.roleId || "role"} from ${revokeRole.dataset.revokeRole || "member"}?`,
      submit: "Revoke",
      payload: {
        type: "enterprise_revoke_role",
        member_id: revokeRole.dataset.revokeRole || "",
        role_id: revokeRole.dataset.roleId || "",
        scope: revokeRole.dataset.scope || "org",
        scope_id: revokeRole.dataset.scopeId || "",
      },
      label: "revoke role",
    };
    render();
    return;
  }
  const revokeDomainAccess = target.closest("[data-revoke-domain-access]");
  if (revokeDomainAccess) {
    event.preventDefault();
    state.modal = {
      kind: "confirm",
      title: "Revoke Domain Access",
      message: `Revoke ${revokeDomainAccess.dataset.subjectType || "subject"}:${revokeDomainAccess.dataset.subjectId || ""} from ${revokeDomainAccess.dataset.revokeDomainAccess || "domain"}?`,
      submit: "Revoke",
      payload: {
        type: "enterprise_revoke_domain_access",
        domain_id: revokeDomainAccess.dataset.revokeDomainAccess || "",
        subject_type: revokeDomainAccess.dataset.subjectType || "member",
        subject_id: revokeDomainAccess.dataset.subjectId || "",
      },
      label: "revoke domain access",
    };
    render();
    return;
  }
  if (target.closest("[data-modal-cancel]")) {
    event.preventDefault();
    state.modal = null;
    render();
    return;
  }
  if (target.closest("[data-modal-confirm]")) {
    event.preventDefault();
    const modal = state.modal || {};
    state.modal = null;
    send(modal.payload || {}, modal.label || "confirm action");
  }
}

function handleDocumentSubmit(event) {
  const form = event.target.closest("form[data-action]");
  if (!form) return;
  event.preventDefault();
  if (form.dataset.action === "modal-submit") {
    const config = modalConfig();
    if (!config) return;
    const payload = config.payload(formData(form));
    state.modal = null;
    send(payload, config.submit.toLowerCase());
    return;
  }
  handleFormSubmit(form);
}

function refreshAll() {
  send({ type: "os_get_runtime_profile" });
  send({ type: "enterprise_get_overview" });
  send({ type: "enterprise_get_settings" });
  send({ type: "enterprise_list_foundation" });
  send({ type: "enterprise_list_org_units" });
  send({ type: "enterprise_list_positions" });
  send({ type: "enterprise_list_members" });
  send({ type: "enterprise_list_roles" });
  send({ type: "enterprise_list_domain_access" });
  send({ type: "os_list_domains" });
  send({ type: "os_audit_events", limit: 50 });
}

function refreshDomainConfigs() {
  state.domains.forEach((item) => {
    const id = item.manifest?.id;
    if (id) send({ type: "enterprise_get_domain_config", domain_id: id });
    if (id) send({ type: "enterprise_get_domain_dependencies", domain_id: id });
  });
  if (domainById("crm")) {
    send({ type: "crm_list_records", entity_type: "prospect", limit: 20 });
    send({ type: "crm_list_records", entity_type: "market_signal", limit: 20 });
    send({ type: "crm_list_records", entity_type: "outbound_draft", limit: 20 });
    send({ type: "crm_list_events", limit: 20 });
  }
}

function handleMessage(data) {
  if (data.type === "error") {
    state.pendingAction = "";
    state.pendingType = "";
    showError(data.message || "Operation failed.");
    render();
    return;
  }
  if (data.type === "os_runtime_profile") state.profile = data;
  if (data.type === "enterprise_overview") {
    state.overview = data;
    state.audit = data.audit?.recent || state.audit;
  }
  if (data.type === "enterprise_settings") {
    state.settings = data.settings || {};
    completePending("enterprise_settings", "Settings saved.");
  }
  if (data.type === "enterprise_foundation") state.foundation = data.items || [];
  if (data.type === "enterprise_org_units") state.units = data.items || [];
  if (data.type === "enterprise_positions") state.positions = data.items || [];
  if (data.type === "os_domains") {
    state.domains = data.items || [];
    refreshDomainConfigs();
  }
  if (data.type === "enterprise_members") state.members = data.items || [];
  if (data.type === "enterprise_roles") {
    state.roles = data.items || [];
    state.assignments = data.assignments || [];
    state.domainAccess = data.domain_access || state.domainAccess || [];
  }
  if (data.type === "enterprise_domain_access") {
    if (data.domain_id) {
      const others = state.domainAccess.filter((item) => item.domain_id !== data.domain_id);
      state.domainAccess = [...others, ...(data.items || [])];
    } else {
      state.domainAccess = data.items || [];
    }
    if (data.item?.ok === false) failPending("enterprise_domain_access", "No matching domain access grant was changed.");
    else completePending("enterprise_domain_access", "Domain access updated.");
  }
  if (data.type === "os_audit_events") state.audit = data.items || [];
  if (data.type === "enterprise_domain_config") {
    if (data.domain_id) state.domainConfigs[data.domain_id] = data;
    if (data.ok === false) failPending("enterprise_domain_config", data.message || "Domain configuration failed.");
    else completePending("enterprise_domain_config", "Domain configuration saved.");
  }
  if (data.type === "enterprise_domain_dependencies") {
    if (data.domain_id) state.domainDependencies[data.domain_id] = data;
  }
  if (data.type === "crm_records") {
    state.crmRecords[data.entity_type || ""] = data.items || [];
  }
  if (data.type === "crm_events") state.crmEvents = data.items || [];
  if (data.type === "os_domain_lifecycle_result") {
    state.domains = data.items || state.domains;
    if (data.result?.ok === false) failPending("os_domain_lifecycle_result", data.result?.message || "Module lifecycle action failed.");
    else completePending("os_domain_lifecycle_result", data.result?.message || "Module lifecycle updated.");
    send({ type: "enterprise_get_overview" });
    send({ type: "os_audit_events", limit: 50 });
  }
  if (data.type === "enterprise_directory_result") {
    if (data.item?.ok === false) failPending("enterprise_directory_result", "No matching directory record was changed.");
    else completePending("enterprise_directory_result", "Change saved.");
    if (data.members) state.members = data.members;
    if (data.org_units) state.units = data.org_units;
    if (data.positions) state.positions = data.positions;
    if (data.roles) state.roles = data.roles;
    if (data.assignments) state.assignments = data.assignments;
    if (data.item?.ok === false && data.item?.error) showError(data.item.error);
    send({ type: "enterprise_get_overview" });
    send({ type: "os_audit_events", limit: 50 });
  }
  render();
}

window.addEventListener("hashchange", () => setRoute(routeFromHash()));
window.addEventListener("popstate", () => {
  state.route = routeFromHash();
  render();
});
window.addEventListener("error", (event) => {
  showError(event.message || "Enterprise Admin script error.");
  render();
});
document.addEventListener("click", handleDocumentClick);
document.addEventListener("submit", handleDocumentSubmit);

state.route = routeFromHash();

function startWebSocket() {
  window.__hc_ws?.close();
  const ws = new WebSocket(wsUrl());
  window.__hc_ws = ws;
  ws.addEventListener("open", refreshAll);
  ws.addEventListener("message", (event) => {
    try {
      handleMessage(JSON.parse(event.data));
    } catch (error) {
      showError(error?.message || "Enterprise Admin message handling failed.");
      render();
    }
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
