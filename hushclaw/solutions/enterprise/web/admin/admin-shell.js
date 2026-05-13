const state = {
  profile: null,
  overview: null,
  domains: [],
  members: [],
  roles: [],
};

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams(location.search);
  const key = params.get("api_key") || "";
  return `${proto}//${location.host}${key ? `?api_key=${encodeURIComponent(key)}` : ""}`;
}

function send(ws, payload) {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function lifecycle(action, domainId) {
  if (!window.__hc_ws || !domainId) return;
  const type = action === "install" ? "os_install_domain"
    : action === "enable" ? "os_enable_domain"
    : "os_disable_domain";
  send(window.__hc_ws, { type, domain_id: domainId, scope: "org" });
}

function render() {
  const status = document.getElementById("runtime-status");
  const cards = document.getElementById("workspace-cards");
  if (!cards) return;
  const distro = state.profile?.distro || {};
  const counts = state.overview?.directory?.counts || {};
  if (status) status.textContent = distro.id ? `${esc(distro.id)} admin` : "Connecting...";

  cards.innerHTML = `
    <article class="enterprise-shell-card">
      <strong>Organization Directory</strong>
      <span>${Number(counts.members || 0)} members · ${Number(counts.units || 0)} units · ${Number(counts.roles || 0)} roles</span>
    </article>
    <article class="enterprise-shell-card">
      <strong>Members</strong>
      <ul>${state.members.map((m) => `<li>${esc(m.display_name || m.id)} · ${esc(m.title || m.status || "")}</li>`).join("")}</ul>
    </article>
    <article class="enterprise-shell-card">
      <strong>Roles</strong>
      <ul>${state.roles.map((r) => `<li>${esc(r.name || r.id)} · ${(r.permissions || []).length} permissions</li>`).join("")}</ul>
    </article>
    ${state.domains.map((item) => {
      const m = item.manifest || {};
      const s = item.status || {};
      const action = s.enabled ? "disable" : s.installed ? "enable" : "install";
      return `
        <article class="enterprise-shell-card">
          <strong>${esc(m.name || m.id)}</strong>
          <span>${esc(m.description || "")}</span>
          <ul>
            <li>${esc(s.enabled ? "Enabled for new sessions" : s.installed ? "Installed, disabled" : "Ready to install")}</li>
            <li>${(m.tools || []).length} tools · ${(m.agents || []).length} agents</li>
          </ul>
          <button data-domain-action="${action}" data-domain-id="${esc(m.id)}">${action}</button>
        </article>
      `;
    }).join("")}
  `;

  cards.querySelectorAll("[data-domain-action]").forEach((btn) => {
    btn.addEventListener("click", () => lifecycle(btn.dataset.domainAction, btn.dataset.domainId));
  });
}

function handleMessage(data) {
  if (data.type === "os_runtime_profile") state.profile = data;
  if (data.type === "enterprise_overview") state.overview = data;
  if (data.type === "os_domains") state.domains = data.items || [];
  if (data.type === "enterprise_members") state.members = data.items || [];
  if (data.type === "enterprise_roles") state.roles = data.items || [];
  if (data.type === "os_domain_lifecycle_result") state.domains = data.items || state.domains;
  render();
}

const ws = new WebSocket(wsUrl());
window.__hc_ws = ws;
ws.addEventListener("open", () => {
  send(ws, { type: "os_get_runtime_profile" });
  send(ws, { type: "enterprise_get_overview" });
  send(ws, { type: "enterprise_list_members" });
  send(ws, { type: "enterprise_list_roles" });
  send(ws, { type: "os_list_domains" });
});
ws.addEventListener("message", (event) => {
  try { handleMessage(JSON.parse(event.data)); } catch {}
});
ws.addEventListener("close", () => {
  const status = document.getElementById("runtime-status");
  if (status) status.textContent = "Disconnected";
});
