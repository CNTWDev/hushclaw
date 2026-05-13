const state = {
  profile: null,
  overview: null,
  domains: [],
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

function render() {
  const status = document.getElementById("runtime-status");
  const cards = document.getElementById("workspace-cards");
  if (!cards) return;

  const distro = state.profile?.distro || {};
  const org = state.overview?.directory?.org || {};
  const enabled = state.domains.filter((item) => item.status?.enabled);
  const planned = state.domains.filter((item) => item.manifest?.status === "planned");

  if (status) status.textContent = distro.id ? `${esc(distro.id)} runtime` : "Connecting...";

  cards.innerHTML = `
    <article class="enterprise-shell-card">
      <strong>${esc(org.name || "Organization")}</strong>
      <span>${esc(distro.name || "HushClaw Enterprise")}</span>
      <ul>
        <li>${esc(state.profile?.current_shell || "enterprise_workspace")} shell</li>
        <li>${esc((state.profile?.capabilities || []).length)} platform capabilities</li>
      </ul>
    </article>
    <article class="enterprise-shell-card">
      <strong>Business Domains</strong>
      <span>${enabled.length} enabled, ${planned.length} planned</span>
      <ul>
        ${state.domains.slice(0, 5).map((item) => `<li>${esc(item.manifest?.name || item.manifest?.id)} · ${item.status?.enabled ? "enabled" : "available"}</li>`).join("")}
      </ul>
    </article>
    <article class="enterprise-shell-card">
      <strong>Knowledge & Agents</strong>
      <span>Shared memory, governed tools, and agent collaboration are provided by AgentOS.</span>
    </article>
    <article class="enterprise-shell-card">
      <strong>Administration</strong>
      <span>Manage members, roles, modules, connectors, and audit in the enterprise admin console.</span>
      <ul><li><a href="/enterprise/admin">Open admin console</a></li></ul>
    </article>
  `;
}

function handleMessage(data) {
  if (data.type === "os_runtime_profile") state.profile = data;
  if (data.type === "enterprise_overview") state.overview = data;
  if (data.type === "os_domains") state.domains = data.items || [];
  render();
}

const ws = new WebSocket(wsUrl());
ws.addEventListener("open", () => {
  send(ws, { type: "os_get_runtime_profile" });
  send(ws, { type: "enterprise_get_overview" });
  send(ws, { type: "os_list_domains" });
});
ws.addEventListener("message", (event) => {
  try { handleMessage(JSON.parse(event.data)); } catch {}
});
ws.addEventListener("close", () => {
  const status = document.getElementById("runtime-status");
  if (status) status.textContent = "Disconnected";
});
