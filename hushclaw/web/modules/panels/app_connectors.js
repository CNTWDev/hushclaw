/**
 * panels/app_connectors.js — Main App Connectors panel.
 */

import {
  appConnectors, appConnectorsPanel, els, escHtml, send,
} from "../state.js";
import { syncFormToState, saveSettings } from "../settings/save.js";
import { openDialog, closeModal } from "../modal.js";
import { withApiKey } from "../http.js";

const CONNECTORS = [
  {
    id: "github",
    name: "GitHub",
    category: "Developer",
    icon: "GH",
    brand: "github",
    tagline: "Built-in repository connector for issues, pull requests, code, commits, and repositories.",
    capabilities: ["Search", "Read", "Sources"],
    runtime: "Read-only REST adapter",
    auth: "GitHub OAuth or fine-grained token",
    statusLabel(c) {
      if (c.enabled && c.token_set) return "Enabled";
      if (c.token_set) return "Configured";
      return "Not connected";
    },
    statusClass(c) {
      if (c.enabled && c.token_set) return "ok";
      if (c.token_set) return "warn";
      return "off";
    },
  },
  {
    id: "reddit",
    name: "Reddit",
    category: "Social / Content Platforms",
    icon: "RD",
    brand: "reddit",
    tagline: "Official Reddit OAuth API adapter for subreddit search, reading posts, posting, and commenting.",
    capabilities: ["Search", "Read", "Post", "Comment"],
    runtime: "Reddit OAuth API adapter",
    auth: "Reddit OAuth access token",
    statusLabel(c) {
      if (c.enabled && c.access_token_set) return "Enabled";
      if (c.access_token_set) return "Configured";
      return "Not connected";
    },
    statusClass(c) {
      if (c.enabled && c.access_token_set) return "ok";
      if (c.access_token_set) return "warn";
      return "off";
    },
  },
  {
    id: "x",
    name: "X",
    category: "Social / Content Platforms",
    icon: "X",
    brand: "x",
    tagline: "Official X API v2 adapter for search, reading posts, posting, and replies.",
    capabilities: ["Search", "Read", "Stream", "Post", "Reply"],
    runtime: "X API v2 adapter with outbound filtered stream",
    auth: "Consumer Key, Consumer Secret, Bearer Token, plus optional user access token",
    statusLabel(c) {
      if (c.enabled && (c.bearer_token_set || c.access_token_set)) return "Enabled";
      if (c.bearer_token_set || c.access_token_set) return "Configured";
      return "Not connected";
    },
    statusClass(c) {
      if (c.enabled && (c.bearer_token_set || c.access_token_set)) return "ok";
      if (c.bearer_token_set || c.access_token_set) return "warn";
      return "off";
    },
  },
  {
    id: "google-workspace",
    stateKey: "google_workspace",
    name: "Google Workspace",
    category: "Productivity",
    icon: "GW",
    brand: "google",
    tagline: "Built-in Google adapter for Drive, Gmail, Calendar, and Docs OAuth credentials.",
    capabilities: ["Drive", "Gmail", "Calendar", "Docs"],
    runtime: "OAuth app connector adapter",
    auth: "Google OAuth",
    statusLabel(c) {
      if (c.enabled && (c.refresh_token_set || c.access_token_set)) return "Enabled";
      if (c.refresh_token_set || c.access_token_set) return "Configured";
      return "Not connected";
    },
    statusClass(c) {
      if (c.enabled && (c.refresh_token_set || c.access_token_set)) return "ok";
      if (c.refresh_token_set || c.access_token_set) return "warn";
      return "off";
    },
  },
  {
    id: "notion",
    name: "Notion",
    category: "Productivity",
    icon: "NT",
    brand: "notion",
    tagline: "Built-in Notion adapter for pages, databases, and team knowledge.",
    capabilities: ["Pages", "Databases", "Search"],
    runtime: "Notion API adapter",
    auth: "OAuth or integration token",
    statusLabel(c) {
      if (c.enabled && c.token_set) return "Enabled";
      if (c.token_set) return "Configured";
      return "Not connected";
    },
    statusClass(c) {
      if (c.enabled && c.token_set) return "ok";
      if (c.token_set) return "warn";
      return "off";
    },
  },
  {
    id: "jira",
    name: "Jira",
    category: "Developer",
    icon: "JR",
    brand: "jira",
    tagline: "Built-in Jira adapter for issue search, reading, and project context.",
    capabilities: ["Issues", "Projects", "Search"],
    runtime: "Jira Cloud REST adapter",
    auth: "OAuth or API token",
    statusLabel(c) {
      if (c.enabled && (c.token_set || c.access_token_set)) return "Enabled";
      if (c.token_set || c.access_token_set) return "Configured";
      return "Not connected";
    },
    statusClass(c) {
      if (c.enabled && (c.token_set || c.access_token_set)) return "ok";
      if (c.token_set || c.access_token_set) return "warn";
      return "off";
    },
  },
];

const CATEGORY_ORDER = ["Developer", "Productivity", "Social / Content Platforms"];

const PLANNED_CONNECTORS = [
  { id: "youtube", name: "YouTube", category: "Social / Content Platforms", capabilities: ["Search", "Upload", "Comments"], note: "Quota-based Google OAuth connector." },
  { id: "pinterest", name: "Pinterest", category: "Social / Content Platforms", capabilities: ["Pins", "Boards", "Analytics"], note: "Business account capabilities vary." },
  { id: "wechat-official", name: "WeChat Official Account", category: "Social / Content Platforms", capabilities: ["Messages", "Articles", "Menus"], note: "Official account APIs; personal account automation is not supported." },
  { id: "douyin", name: "Douyin", category: "Social / Content Platforms", capabilities: ["Publish", "Insights"], note: "Permission-tiered Open Platform access." },
  { id: "xiaohongshu", name: "Xiaohongshu", category: "Social / Content Platforms", capabilities: ["Brand Publish", "Analytics"], note: "Brand/merchant/service-provider access only." },
  { id: "bilibili", name: "Bilibili", category: "Social / Content Platforms", capabilities: ["Upload", "Comments", "Live"], note: "Submission APIs require platform approval." },
];

function _connectorById(id) {
  return CONNECTORS.find((item) => item.id === id) || CONNECTORS[0];
}

function _statusText(type, text) {
  if (!text) return "";
  return `<span class="app-connector-inline-status ${type || ""}">${escHtml(text)}</span>`;
}

function _renderGroupedCards() {
  const categories = new Map();
  CONNECTORS.forEach((item) => {
    const category = item.category || "Other";
    if (!categories.has(category)) categories.set(category, []);
    categories.get(category).push(item);
  });
  PLANNED_CONNECTORS.forEach((item) => {
    const category = item.category || "Other";
    if (!categories.has(category)) categories.set(category, []);
  });
  const ordered = [
    ...CATEGORY_ORDER.filter((cat) => categories.has(cat)),
    ...[...categories.keys()].filter((cat) => !CATEGORY_ORDER.includes(cat)).sort(),
  ];
  return ordered.map((category) => `
    <section class="app-connectors-group">
      <div class="app-connectors-group-head">
        <h2>${escHtml(category)}</h2>
        <span>${categories.get(category).length} active connector${categories.get(category).length === 1 ? "" : "s"}</span>
      </div>
      <div class="app-connectors-grid${categories.get(category).length ? "" : " hidden"}">
        ${categories.get(category).map((item) => _renderCard(item)).join("")}
      </div>
      ${_renderPlannedConnectors(category)}
    </section>
  `).join("");
}

function _renderPlannedConnectors(category) {
  const planned = PLANNED_CONNECTORS.filter((item) => item.category === category);
  if (!planned.length) return "";
  return `
    <div class="app-connectors-planned">
      <div class="app-connectors-planned-title">Planned platform-specific connectors</div>
      <div class="app-connectors-planned-grid">
        ${planned.map((item) => `
          <div class="app-connector-planned-card">
            <div class="app-connector-planned-name">${escHtml(item.name)}</div>
            <div class="app-connector-planned-note">${escHtml(item.note)}</div>
            <div class="app-connector-chips">
              ${item.capabilities.map((cap) => `<span>${escHtml(cap)}</span>`).join("")}
            </div>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function _renderCard(item) {
  const stateKey = item.stateKey || item.id;
  const cfg = appConnectors[stateKey] || {};
  const status = item.statusLabel ? item.statusLabel(cfg) : "Not connected";
  const statusClass = item.statusClass ? item.statusClass(cfg) : "off";
  return `
    <button class="app-connector-card app-connector-card-${escHtml(item.brand || item.id)}"
            data-app-connector="${escHtml(item.id)}">
      <div class="app-connector-card-top">
        <span class="app-connector-mark" aria-hidden="true">${escHtml(item.icon)}</span>
        <div class="app-connector-title-block">
          <span class="app-connector-card-type">${escHtml(item.category || "Built-in")} connector</span>
          <span class="app-connector-card-name">${escHtml(item.name)}</span>
        </div>
      </div>
      <span class="app-connector-status ${statusClass}">${escHtml(status)}</span>
      <div class="app-connector-card-desc">${escHtml(item.tagline)}</div>
      <div class="app-connector-chips">
        ${item.capabilities.map((cap) => `<span>${escHtml(cap)}</span>`).join("")}
      </div>
      <div class="app-connector-card-footer">
        <span>Configure connection</span>
        <span aria-hidden="true">→</span>
      </div>
    </button>
  `;
}

function _renderGitHubConfigModal() {
  const gh = appConnectors.github;
  const tokenPlaceholder = gh.token_set ? "Token already set; leave blank to keep it" : "GitHub fine-grained token";
  const tokenState = gh.token_set ? "Token stored outside hushclaw.toml" : "No token stored yet";
  const oauthReady = _oauthReady(gh);

  return `
    <div class="app-connector-modal">
      <div class="app-connector-modal-summary">
        <div>
          <div class="app-connector-kicker">Built-in GitHub connector</div>
          <h2>Repository search and read tools</h2>
          <p>This connector is shipped by HushClaw. Users connect an account and repository; they do not create connector code here.</p>
        </div>
        <label class="toggle">
          <input type="checkbox" id="app-github-enabled" ${gh.enabled ? "checked" : ""}>
          <span class="slider"></span>
        </label>
      </div>

      <div class="app-connector-info-grid">
        <div>
          <span>Runtime</span>
          <strong>Read-only GitHub REST adapter</strong>
        </div>
        <div>
          <span>Registered tools</span>
          <strong>github_search, github_read</strong>
        </div>
        <div>
          <span>Activation</span>
          <strong>New chat sessions after save</strong>
        </div>
      </div>

      ${_renderOAuthConnectBlock("github", oauthReady, gh.token_set, "Connect GitHub")}

      <details class="app-connector-advanced">
        <summary>Advanced manual configuration</summary>
      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>Authorization mode</span>
          <select id="app-github-auth-mode">
            <option value="managed" ${gh.auth_mode === "managed" ? "selected" : ""}>Managed by HushClaw broker</option>
            <option value="custom" ${gh.auth_mode === "custom" ? "selected" : ""}>Custom OAuth app / token</option>
          </select>
        </label>
        <label class="settings-field">
          <span>Auth type</span>
          <select id="app-github-auth-type">
            <option value="pat" ${gh.auth_type === "pat" ? "selected" : ""}>Fine-grained token</option>
            <option value="oauth" ${gh.auth_type === "oauth" ? "selected" : ""}>OAuth</option>
          </select>
        </label>
        <label class="settings-field">
          <span>Default repository</span>
          <input id="app-github-default-repo" type="text" value="${escHtml(gh.default_repo || "")}" placeholder="owner/repo">
        </label>
      </div>

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>OAuth client ID</span>
          <input id="app-github-client-id" type="password" value="" placeholder="${escHtml(_secretPlaceholder(gh.client_id_set, "GitHub OAuth client ID"))}">
        </label>
        <label class="settings-field">
          <span>OAuth client ID reference</span>
          <input id="app-github-client-id-ref" type="text" value="${escHtml(gh.client_id_ref || "app_connectors.github.client_id")}" placeholder="app_connectors.github.client_id">
        </label>
      </div>

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>OAuth client secret</span>
          <input id="app-github-client-secret" type="password" value="" placeholder="${escHtml(_secretPlaceholder(gh.client_secret_set, "GitHub OAuth client secret"))}">
        </label>
        <label class="settings-field">
          <span>OAuth client secret reference</span>
          <input id="app-github-client-secret-ref" type="text" value="${escHtml(gh.client_secret_ref || "app_connectors.github.client_secret")}" placeholder="app_connectors.github.client_secret">
        </label>
      </div>

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>Secret reference</span>
          <input id="app-github-token-ref" type="text" value="${escHtml(gh.token_ref || "app_connectors.github.token")}" placeholder="app_connectors.github.token">
        </label>
      </div>

      <label class="settings-field">
        <span>Access token</span>
        <input id="app-github-token" type="password" value="" placeholder="${escHtml(tokenPlaceholder)}">
        <span class="settings-hint">${escHtml(tokenState)}. Use a read-only fine-grained token with repository metadata/content/issues permissions as needed.</span>
      </label>

      <label class="settings-field app-connector-disabled-action">
        <span><input type="checkbox" id="app-github-allow-actions" disabled> Enable write actions</span>
        <span class="settings-hint">Actions stay disabled in v1. This keeps the connector read-only.</span>
      </label>
      </details>

      <div class="app-connector-actions">
        <button id="btn-save-app-github">Save connector</button>
        <button id="btn-test-app-github" class="secondary">Test connection</button>
        <span id="app-connector-modal-save-status">${_statusText(appConnectorsPanel.saveStatusType, appConnectorsPanel.saveStatus)}</span>
        <span id="app-connector-modal-test-status">${_statusText(appConnectorsPanel.testStatusType, appConnectorsPanel.testStatus)}</span>
      </div>
    </div>
  `;
}

function _oauthReady(c) {
  return (c.auth_mode || "managed") === "managed" || (c.client_id_set && c.client_secret_set);
}

function _renderOAuthConnectBlock(id, oauthReady, connected, label) {
  return `
    <div class="app-connector-oauth-panel">
      <div>
        <div class="app-connector-kicker">${connected ? "Connected account" : "Preferred setup"}</div>
        <strong>${connected ? "Authorization is stored in the local secret store." : "Use the provider authorization page."}</strong>
        <p>${oauthReady ? "Click connect to authorize in the provider's own consent screen." : "Switch to managed mode or add a custom OAuth client ID and secret in Advanced configuration first."}</p>
      </div>
      <button id="btn-oauth-app-${id}" ${oauthReady ? "" : "disabled"}>${escHtml(label)}</button>
    </div>
  `;
}

function _secretPlaceholder(isSet, label) {
  return isSet ? `${label} already set; leave blank to keep it` : label;
}

function _commonModalSummary(item, enabledId, enabled) {
  return `
    <div class="app-connector-modal-summary">
      <div>
        <div class="app-connector-kicker">Built-in ${escHtml(item.name)} connector</div>
        <h2>${escHtml(item.name)} workspace connection</h2>
        <p>${escHtml(item.tagline)} HushClaw owns the adapter; users only authorize their workspace.</p>
      </div>
      <label class="toggle">
        <input type="checkbox" id="${enabledId}" ${enabled ? "checked" : ""}>
        <span class="slider"></span>
      </label>
    </div>
  `;
}

function _commonInfoGrid(item, ownership = "Provided by HushClaw, not user-created") {
  return `
    <div class="app-connector-info-grid">
      <div>
        <span>Runtime</span>
        <strong>${escHtml(item.runtime)}</strong>
      </div>
      <div>
        <span>Authentication</span>
        <strong>${escHtml(item.auth)}</strong>
      </div>
      <div>
        <span>Connector ownership</span>
        <strong>${escHtml(ownership)}</strong>
      </div>
    </div>
  `;
}

function _renderGoogleWorkspaceConfigModal(item) {
  const c = appConnectors.google_workspace;
  const oauthReady = _oauthReady(c);
  return `
    <div class="app-connector-modal">
      ${_commonModalSummary(item, "app-google-workspace-enabled", c.enabled)}
      ${_commonInfoGrid(item, "Google SDK adapter with OAuth credentials")}
      ${_renderOAuthConnectBlock("google-workspace", oauthReady, c.refresh_token_set || c.access_token_set, "Connect Google Workspace")}

      <details class="app-connector-advanced">
        <summary>Advanced OAuth and token configuration</summary>
      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>Authorization mode</span>
          <select id="app-google-workspace-auth-mode">
            <option value="managed" ${c.auth_mode === "managed" ? "selected" : ""}>Managed by HushClaw broker</option>
            <option value="custom" ${c.auth_mode === "custom" ? "selected" : ""}>Custom OAuth app</option>
          </select>
        </label>
        <label class="settings-field">
          <span>Auth type</span>
          <select id="app-google-workspace-auth-type">
            <option value="oauth" ${c.auth_type === "oauth" ? "selected" : ""}>OAuth 2.0</option>
          </select>
        </label>
        <label class="settings-field">
          <span>Scopes</span>
          <input id="app-google-workspace-scopes" type="text" value="${escHtml((c.scopes || []).join(" "))}" placeholder="https://www.googleapis.com/auth/drive.readonly">
        </label>
      </div>

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>Client ID</span>
          <input id="app-google-workspace-client-id" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.client_id_set, "OAuth client ID"))}">
        </label>
        <label class="settings-field">
          <span>Client ID secret reference</span>
          <input id="app-google-workspace-client-id-ref" type="text" value="${escHtml(c.client_id_ref)}">
        </label>
      </div>

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>Client secret</span>
          <input id="app-google-workspace-client-secret" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.client_secret_set, "OAuth client secret"))}">
        </label>
        <label class="settings-field">
          <span>Client secret reference</span>
          <input id="app-google-workspace-client-secret-ref" type="text" value="${escHtml(c.client_secret_ref)}">
        </label>
      </div>

      <div class="app-connector-info-grid">
        <div><span>Access token</span><strong>${c.access_token_set ? "Stored" : "Not stored"}</strong></div>
        <div><span>Refresh token</span><strong>${c.refresh_token_set ? "Stored" : "Not stored"}</strong></div>
        <div><span>SDK package</span><strong>google-api-python-client</strong></div>
      </div>

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>Access token</span>
          <input id="app-google-workspace-access-token" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.access_token_set, "OAuth access token"))}">
          <input id="app-google-workspace-access-token-ref" type="hidden" value="${escHtml(c.access_token_ref)}">
        </label>
        <label class="settings-field">
          <span>Refresh token</span>
          <input id="app-google-workspace-refresh-token" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.refresh_token_set, "OAuth refresh token"))}">
          <input id="app-google-workspace-refresh-token-ref" type="hidden" value="${escHtml(c.refresh_token_ref)}">
        </label>
      </div>
      </details>

      ${_renderConnectorActions("google-workspace")}
    </div>
  `;
}

function _renderNotionConfigModal(item) {
  const c = appConnectors.notion;
  const oauthReady = _oauthReady(c);
  return `
    <div class="app-connector-modal">
      ${_commonModalSummary(item, "app-notion-enabled", c.enabled)}
      ${_commonInfoGrid(item, "Notion SDK adapter with workspace token")}
      ${_renderOAuthConnectBlock("notion", oauthReady, c.token_set, "Connect Notion")}

      <details class="app-connector-advanced">
        <summary>Advanced OAuth and token configuration</summary>
      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>Authorization mode</span>
          <select id="app-notion-auth-mode">
            <option value="managed" ${c.auth_mode === "managed" ? "selected" : ""}>Managed by HushClaw broker</option>
            <option value="custom" ${c.auth_mode === "custom" ? "selected" : ""}>Custom OAuth app / token</option>
          </select>
        </label>
        <label class="settings-field">
          <span>Auth type</span>
          <select id="app-notion-auth-type">
            <option value="internal_token" ${c.auth_type === "internal_token" ? "selected" : ""}>Internal integration token</option>
            <option value="oauth" ${c.auth_type === "oauth" ? "selected" : ""}>OAuth token</option>
          </select>
        </label>
        <label class="settings-field">
          <span>Workspace label</span>
          <input id="app-notion-workspace-name" type="text" value="${escHtml(c.workspace_name || "")}" placeholder="Product wiki">
        </label>
      </div>

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>OAuth client ID</span>
          <input id="app-notion-client-id" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.client_id_set, "Notion OAuth client ID"))}">
        </label>
        <label class="settings-field">
          <span>OAuth client ID reference</span>
          <input id="app-notion-client-id-ref" type="text" value="${escHtml(c.client_id_ref || "app_connectors.notion.client_id")}">
        </label>
      </div>

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>OAuth client secret</span>
          <input id="app-notion-client-secret" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.client_secret_set, "Notion OAuth client secret"))}">
        </label>
        <label class="settings-field">
          <span>OAuth client secret reference</span>
          <input id="app-notion-client-secret-ref" type="text" value="${escHtml(c.client_secret_ref || "app_connectors.notion.client_secret")}">
        </label>
      </div>

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>Notion token</span>
          <input id="app-notion-token" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.token_set, "Notion integration token"))}">
        </label>
        <label class="settings-field">
          <span>Token secret reference</span>
          <input id="app-notion-token-ref" type="text" value="${escHtml(c.token_ref)}">
        </label>
      </div>
      </details>

      ${_renderConnectorActions("notion")}
    </div>
  `;
}

function _renderJiraConfigModal(item) {
  const c = appConnectors.jira;
  const oauthReady = _oauthReady(c);
  return `
    <div class="app-connector-modal">
      ${_commonModalSummary(item, "app-jira-enabled", c.enabled)}
      ${_commonInfoGrid(item, "Jira Cloud REST adapter")}
      ${_renderOAuthConnectBlock("jira", oauthReady, c.access_token_set || c.token_set, "Connect Jira")}

      <details class="app-connector-advanced">
        <summary>Advanced OAuth and token configuration</summary>
      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>Authorization mode</span>
          <select id="app-jira-auth-mode">
            <option value="managed" ${c.auth_mode === "managed" ? "selected" : ""}>Managed by HushClaw broker</option>
            <option value="custom" ${c.auth_mode === "custom" ? "selected" : ""}>Custom OAuth app / token</option>
          </select>
        </label>
        <label class="settings-field">
          <span>Auth type</span>
          <select id="app-jira-auth-type">
            <option value="api_token" ${c.auth_type === "api_token" ? "selected" : ""}>API token</option>
            <option value="oauth" ${c.auth_type === "oauth" ? "selected" : ""}>OAuth access token</option>
          </select>
        </label>
        <label class="settings-field">
          <span>Jira site URL</span>
          <input id="app-jira-site-url" type="text" value="${escHtml(c.site_url || "")}" placeholder="https://your-domain.atlassian.net">
        </label>
      </div>

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>Account email</span>
          <input id="app-jira-email" type="text" value="${escHtml(c.email || "")}" placeholder="you@example.com">
        </label>
        <label class="settings-field">
          <span>Cloud ID</span>
          <input id="app-jira-cloud-id" type="text" value="${escHtml(c.cloud_id || "")}" placeholder="Optional for OAuth">
        </label>
      </div>

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>OAuth client ID</span>
          <input id="app-jira-client-id" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.client_id_set, "Atlassian OAuth client ID"))}">
          <input id="app-jira-client-id-ref" type="hidden" value="${escHtml(c.client_id_ref || "app_connectors.jira.client_id")}">
        </label>
        <label class="settings-field">
          <span>OAuth client secret</span>
          <input id="app-jira-client-secret" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.client_secret_set, "Atlassian OAuth client secret"))}">
          <input id="app-jira-client-secret-ref" type="hidden" value="${escHtml(c.client_secret_ref || "app_connectors.jira.client_secret")}">
        </label>
      </div>

      <label class="settings-field">
        <span>OAuth scopes</span>
        <input id="app-jira-scopes" type="text" value="${escHtml((c.scopes || []).join(" "))}" placeholder="read:jira-work read:jira-user offline_access">
      </label>

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>API token</span>
          <input id="app-jira-token" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.token_set, "Jira API token"))}">
          <input id="app-jira-token-ref" type="hidden" value="${escHtml(c.token_ref)}">
        </label>
        <label class="settings-field">
          <span>OAuth access token</span>
          <input id="app-jira-access-token" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.access_token_set, "OAuth access token"))}">
          <input id="app-jira-access-token-ref" type="hidden" value="${escHtml(c.access_token_ref)}">
        </label>
      </div>

      <label class="settings-field">
        <span>OAuth refresh token</span>
        <input id="app-jira-refresh-token" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.refresh_token_set, "OAuth refresh token"))}">
        <input id="app-jira-refresh-token-ref" type="hidden" value="${escHtml(c.refresh_token_ref || "app_connectors.jira.refresh_token")}">
      </label>
      </details>

      ${_renderConnectorActions("jira")}
    </div>
  `;
}

function _renderRedditConfigModal(item) {
  const c = appConnectors.reddit;
  return `
    <div class="app-connector-modal">
      ${_commonModalSummary(item, "app-reddit-enabled", c.enabled)}
      ${_commonInfoGrid(item, "Official Reddit OAuth API adapter")}

      <div class="app-connector-info-grid">
        <div><span>Registered tools</span><strong>reddit_search, reddit_read</strong></div>
        <div><span>Write tools</span><strong>reddit_post, reddit_comment</strong></div>
        <div><span>Write guard</span><strong>allow_actions required</strong></div>
      </div>

      <details class="app-connector-advanced" open>
        <summary>OAuth token configuration</summary>
        <div class="app-connector-form-grid">
          <label class="settings-field">
            <span>Authorization mode</span>
            <select id="app-reddit-auth-mode">
              <option value="custom" ${c.auth_mode === "custom" ? "selected" : ""}>Custom Reddit OAuth app</option>
            </select>
          </label>
          <label class="settings-field">
            <span>Auth type</span>
            <select id="app-reddit-auth-type">
              <option value="oauth" ${c.auth_type === "oauth" ? "selected" : ""}>OAuth access token</option>
            </select>
          </label>
          <label class="settings-field">
            <span>Default subreddit</span>
            <input id="app-reddit-default-subreddit" type="text" value="${escHtml(c.default_subreddit || "")}" placeholder="hushclaw">
          </label>
        </div>

        <label class="settings-field">
          <span>User-Agent</span>
          <input id="app-reddit-user-agent" type="text" value="${escHtml(c.user_agent || "HushClaw-AppConnector/1.0")}" placeholder="platform:app:version (by /u/name)">
          <span class="settings-hint">Reddit expects a descriptive User-Agent for API clients.</span>
        </label>

        <div class="app-connector-form-grid">
          <label class="settings-field">
            <span>Client ID</span>
            <input id="app-reddit-client-id" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.client_id_set, "Reddit app client ID"))}">
            <input id="app-reddit-client-id-ref" type="hidden" value="${escHtml(c.client_id_ref || "app_connectors.reddit.client_id")}">
          </label>
          <label class="settings-field">
            <span>Client secret</span>
            <input id="app-reddit-client-secret" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.client_secret_set, "Reddit app client secret"))}">
            <input id="app-reddit-client-secret-ref" type="hidden" value="${escHtml(c.client_secret_ref || "app_connectors.reddit.client_secret")}">
          </label>
        </div>

        <div class="app-connector-form-grid">
          <label class="settings-field">
            <span>Access token</span>
            <input id="app-reddit-access-token" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.access_token_set, "Reddit OAuth access token"))}">
            <input id="app-reddit-access-token-ref" type="hidden" value="${escHtml(c.access_token_ref || "app_connectors.reddit.access_token")}">
          </label>
          <label class="settings-field">
            <span>Refresh token</span>
            <input id="app-reddit-refresh-token" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.refresh_token_set, "Reddit OAuth refresh token"))}">
            <input id="app-reddit-refresh-token-ref" type="hidden" value="${escHtml(c.refresh_token_ref || "app_connectors.reddit.refresh_token")}">
          </label>
        </div>

        <label class="settings-field">
          <span><input type="checkbox" id="app-reddit-allow-actions" ${c.allow_actions ? "checked" : ""}> Enable post/comment actions</span>
          <span class="settings-hint">Read tools work without this. Posting and commenting are blocked until this is enabled.</span>
        </label>
      </details>

      ${_renderConnectorActions("reddit")}
    </div>
  `;
}

function _renderXConfigModal(item) {
  const c = appConnectors.x;
  const callbackUrl = `${location.origin}/oauth/app-connectors/x/callback`;
  const websiteUrl = location.origin;
  return `
    <div class="app-connector-modal">
      ${_commonModalSummary(item, "app-x-enabled", c.enabled)}
      ${_commonInfoGrid(item, "Official X API v2 adapter")}

      <div class="app-connector-info-grid">
        <div><span>Registered tools</span><strong>x_search, x_read_post</strong></div>
        <div><span>Write tools</span><strong>x_post, x_reply</strong></div>
        <div><span>Stream</span><strong>Filtered Stream → App Inbox</strong></div>
        <div><span>Write guard</span><strong>Draft confirmation by default</strong></div>
      </div>

      <div class="app-connector-oauth-panel">
        <div>
          <span>X Developer Portal URLs</span>
          <strong>Callback URL: ${escHtml(callbackUrl)}</strong>
          <p>Website URL: ${escHtml(websiteUrl)}</p>
          <p>In X Developer Portal, enable OAuth 2.0, set App permissions to Read and write, use Web App / confidential client, and add these exact URLs. The OAuth 2.0 Client ID is different from the Consumer Key.</p>
        </div>
      </div>

      ${_renderOAuthConnectBlock("x", Boolean(c.oauth_client_id_set || c.oauth_client_id), c.access_token_set, "Connect X user OAuth")}

      <details class="app-connector-advanced" open>
        <summary>API token configuration</summary>
        <div class="app-connector-form-grid">
          <label class="settings-field">
            <span>Authorization mode</span>
            <select id="app-x-auth-mode">
              <option value="custom" ${(c.auth_mode || "custom") === "custom" ? "selected" : ""}>Local OAuth 2.0 PKCE</option>
              <option value="managed" ${c.auth_mode === "managed" ? "selected" : ""}>Managed broker</option>
            </select>
          </label>
          <label class="settings-field">
            <span>Auth type</span>
            <select id="app-x-auth-type">
              <option value="app_keys" ${c.auth_type === "app_keys" ? "selected" : ""}>App keys + Bearer token</option>
              <option value="oauth2_user" ${c.auth_type === "oauth2_user" ? "selected" : ""}>OAuth 2.0 user token</option>
            </select>
          </label>
        </div>

        <div class="app-connector-form-grid">
          <label class="settings-field">
            <span>Consumer Key</span>
            <input id="app-x-consumer-key" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.consumer_key_set, "X Consumer Key"))}">
            <input id="app-x-consumer-key-ref" type="hidden" value="${escHtml(c.consumer_key_ref || "app_connectors.x.consumer_key")}">
          </label>
          <label class="settings-field">
            <span>Consumer Secret</span>
            <input id="app-x-consumer-secret" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.consumer_secret_set, "X Consumer Secret"))}">
            <input id="app-x-consumer-secret-ref" type="hidden" value="${escHtml(c.consumer_secret_ref || "app_connectors.x.consumer_secret")}">
          </label>
        </div>

        <div class="app-connector-form-grid">
          <label class="settings-field">
            <span>OAuth 2.0 Client ID</span>
            <input id="app-x-oauth-client-id" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.oauth_client_id_set, "X OAuth 2.0 Client ID"))}">
            <input id="app-x-oauth-client-id-ref" type="hidden" value="${escHtml(c.oauth_client_id_ref || "app_connectors.x.oauth_client_id")}">
          </label>
          <label class="settings-field">
            <span>OAuth 2.0 Client Secret</span>
            <input id="app-x-oauth-client-secret" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.oauth_client_secret_set, "X OAuth 2.0 Client Secret"))}">
            <input id="app-x-oauth-client-secret-ref" type="hidden" value="${escHtml(c.oauth_client_secret_ref || "app_connectors.x.oauth_client_secret")}">
          </label>
        </div>

        <label class="settings-field">
          <span>Bearer token</span>
          <input id="app-x-bearer-token" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.bearer_token_set, "X bearer token for read tools"))}">
          <input id="app-x-bearer-token-ref" type="hidden" value="${escHtml(c.bearer_token_ref || "app_connectors.x.bearer_token")}">
        </label>

        <div class="app-connector-form-grid">
          <label class="settings-field">
            <span>OAuth access token</span>
            <input id="app-x-access-token" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.access_token_set, "X OAuth access token"))}">
            <input id="app-x-access-token-ref" type="hidden" value="${escHtml(c.access_token_ref || "app_connectors.x.access_token")}">
          </label>
          <label class="settings-field">
            <span>OAuth refresh token</span>
            <input id="app-x-refresh-token" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.refresh_token_set, "X OAuth refresh token"))}">
            <input id="app-x-refresh-token-ref" type="hidden" value="${escHtml(c.refresh_token_ref || "app_connectors.x.refresh_token")}">
          </label>
        </div>

        <label class="settings-field">
          <span><input type="checkbox" id="app-x-allow-actions" ${c.allow_actions ? "checked" : ""}> Enable post/reply actions</span>
          <span class="settings-hint">Consumer Key/Secret and Bearer Token match the X Developer Portal keys. Posting and replying also require a user-context access token and this setting.</span>
        </label>

        <label class="settings-field">
          <span><input type="checkbox" id="app-x-require-publish-confirmation" ${c.require_publish_confirmation !== false ? "checked" : ""}> Require confirmation before publishing</span>
          <span class="settings-hint">When enabled, x_post and x_reply create local drafts in the App Connector inbox instead of immediately publishing.</span>
        </label>
      </details>

      <details class="app-connector-advanced" open>
        <summary>Filtered Stream</summary>
        <label class="settings-field">
          <span><input type="checkbox" id="app-x-stream-enabled" ${c.stream_enabled ? "checked" : ""}> Enable outbound X stream listener</span>
          <span class="settings-hint">Uses GET /2/tweets/search/stream from this local machine. No public IP or webhook endpoint is required.</span>
        </label>
        <label class="settings-field">
          <span>Stream rules</span>
          <textarea id="app-x-stream-rules" rows="6" placeholder="brand::from:example has:links&#10;support::@yourhandle -is:retweet">${escHtml(_formatXRules(c.stream_rules || []))}</textarea>
          <span class="settings-hint">One rule per line. Optional label format: tag::X query. HushClaw only manages rules tagged with its own prefix.</span>
        </label>
      </details>

      ${_renderConnectorActions("x")}
    </div>
  `;
}

function _formatXRules(rules) {
  if (!Array.isArray(rules)) return "";
  return rules.map((rule, idx) => {
    if (typeof rule === "string") return rule;
    const value = String(rule?.value || rule?.query || "").trim();
    const tag = String(rule?.tag || "").replace(/^hushclaw:/, "").trim();
    if (!value) return "";
    return tag ? `${tag}::${value}` : value;
  }).filter(Boolean).join("\n");
}

function _startOAuth(id) {
  syncFormToState();
  saveSettings();
  appConnectorsPanel.saveStatus = "Opening provider authorization...";
  appConnectorsPanel.saveStatusType = "";
  _setModalStatus("app-connector-modal-save-status", "", appConnectorsPanel.saveStatus);
  const apiKey = new URLSearchParams(location.search).get("api_key") || "";
  const url = withApiKey(`/oauth/app-connectors/${id}/start`, apiKey);
  window.open(url, "_blank", "noopener,noreferrer");
  window.setTimeout(() => send({ type: "get_config_status" }), 1800);
}

function _renderConnectorActions(id) {
  return `
    <div class="app-connector-actions">
      <button id="btn-save-app-${id}">Save connector</button>
      <button id="btn-test-app-${id}" class="secondary">Test connection</button>
      <span id="app-connector-modal-save-status">${_statusText(appConnectorsPanel.saveStatusType, appConnectorsPanel.saveStatus)}</span>
      <span id="app-connector-modal-test-status">${_statusText(appConnectorsPanel.testStatusType, appConnectorsPanel.testStatus)}</span>
    </div>
  `;
}

function _setModalStatus(id, type, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = _statusText(type, text);
}

function _bindGitHubConfig() {
  document.getElementById("btn-save-app-github")?.addEventListener("click", () => {
    appConnectorsPanel.saveStatus = "Saving...";
    appConnectorsPanel.saveStatusType = "";
    _setModalStatus("app-connector-modal-save-status", "", appConnectorsPanel.saveStatus);
    saveSettings();
    appConnectorsPanel.saveStatus = "Save requested. Start a new chat after it completes.";
    appConnectorsPanel.saveStatusType = "ok";
    _setModalStatus("app-connector-modal-save-status", "ok", appConnectorsPanel.saveStatus);
    renderAppConnectorsPanel();
  });

  document.getElementById("btn-test-app-github")?.addEventListener("click", () => {
    syncFormToState();
    appConnectorsPanel.testStatus = "Testing...";
    appConnectorsPanel.testStatusType = "";
    _setModalStatus("app-connector-modal-test-status", "", appConnectorsPanel.testStatus);
    send({
      type: "test_app_connector",
      target: "github",
      enabled: appConnectors.github.enabled,
      auth_mode: appConnectors.github.auth_mode || "managed",
      auth_type: appConnectors.github.auth_type || "pat",
      token_ref: appConnectors.github.token_ref || "app_connectors.github.token",
      token: appConnectors.github.token || "",
      client_id_ref: appConnectors.github.client_id_ref || "app_connectors.github.client_id",
      client_secret_ref: appConnectors.github.client_secret_ref || "app_connectors.github.client_secret",
      client_id: appConnectors.github.client_id || "",
      client_secret: appConnectors.github.client_secret || "",
      default_repo: appConnectors.github.default_repo || "",
      allow_actions: false,
    });
  });
  document.getElementById("btn-oauth-app-github")?.addEventListener("click", () => _startOAuth("github"));
}

function _testPayload(id) {
  syncFormToState();
  if (id === "github") {
    return {
      type: "test_app_connector",
      target: "github",
      enabled: appConnectors.github.enabled,
      auth_mode: appConnectors.github.auth_mode || "managed",
      auth_type: appConnectors.github.auth_type || "pat",
      client_id_ref: appConnectors.github.client_id_ref || "app_connectors.github.client_id",
      client_secret_ref: appConnectors.github.client_secret_ref || "app_connectors.github.client_secret",
      token_ref: appConnectors.github.token_ref || "app_connectors.github.token",
      client_id: appConnectors.github.client_id || "",
      client_secret: appConnectors.github.client_secret || "",
      token: appConnectors.github.token || "",
      default_repo: appConnectors.github.default_repo || "",
      allow_actions: false,
    };
  }
  if (id === "google-workspace") {
    const c = appConnectors.google_workspace;
    return {
      type: "test_app_connector",
      target: "google_workspace",
      enabled: c.enabled,
      auth_mode: c.auth_mode || "managed",
      auth_type: c.auth_type || "oauth",
      client_id_ref: c.client_id_ref,
      client_secret_ref: c.client_secret_ref,
      access_token_ref: c.access_token_ref,
      refresh_token_ref: c.refresh_token_ref,
      client_id: c.client_id || "",
      client_secret: c.client_secret || "",
      access_token: c.access_token || "",
      refresh_token: c.refresh_token || "",
      scopes: c.scopes || [],
      allow_actions: false,
    };
  }
  if (id === "notion") {
    const c = appConnectors.notion;
    return {
      type: "test_app_connector",
      target: "notion",
      enabled: c.enabled,
      auth_mode: c.auth_mode || "managed",
      auth_type: c.auth_type || "internal_token",
      client_id_ref: c.client_id_ref,
      client_secret_ref: c.client_secret_ref,
      client_id: c.client_id || "",
      client_secret: c.client_secret || "",
      token_ref: c.token_ref,
      token: c.token || "",
      workspace_name: c.workspace_name || "",
      allow_actions: false,
    };
  }
  if (id === "jira") {
    const c = appConnectors.jira;
    return {
      type: "test_app_connector",
      target: "jira",
      enabled: c.enabled,
      auth_mode: c.auth_mode || "managed",
      auth_type: c.auth_type || "api_token",
      site_url: c.site_url || "",
      email: c.email || "",
      client_id_ref: c.client_id_ref,
      client_secret_ref: c.client_secret_ref,
      client_id: c.client_id || "",
      client_secret: c.client_secret || "",
      token_ref: c.token_ref,
      token: c.token || "",
      access_token_ref: c.access_token_ref,
      access_token: c.access_token || "",
      refresh_token_ref: c.refresh_token_ref,
      refresh_token: c.refresh_token || "",
      cloud_id: c.cloud_id || "",
      scopes: c.scopes || [],
      allow_actions: false,
    };
  }
  if (id === "reddit") {
    const c = appConnectors.reddit;
    return {
      type: "test_app_connector",
      target: "reddit",
      enabled: c.enabled,
      auth_mode: c.auth_mode || "custom",
      auth_type: c.auth_type || "oauth",
      client_id_ref: c.client_id_ref,
      client_secret_ref: c.client_secret_ref,
      access_token_ref: c.access_token_ref,
      refresh_token_ref: c.refresh_token_ref,
      client_id: c.client_id || "",
      client_secret: c.client_secret || "",
      access_token: c.access_token || "",
      refresh_token: c.refresh_token || "",
      user_agent: c.user_agent || "HushClaw-AppConnector/1.0",
      default_subreddit: c.default_subreddit || "",
      allow_actions: c.allow_actions || false,
    };
  }
  const c = appConnectors.x;
  return {
    type: "test_app_connector",
    target: "x",
    enabled: c.enabled,
    auth_mode: c.auth_mode || "custom",
    auth_type: c.auth_type || "app_keys",
    consumer_key_ref: c.consumer_key_ref,
    consumer_secret_ref: c.consumer_secret_ref,
    consumer_key: c.consumer_key || "",
    consumer_secret: c.consumer_secret || "",
    oauth_client_id_ref: c.oauth_client_id_ref,
    oauth_client_secret_ref: c.oauth_client_secret_ref,
    oauth_client_id: c.oauth_client_id || "",
    oauth_client_secret: c.oauth_client_secret || "",
    bearer_token_ref: c.bearer_token_ref,
    bearer_token: c.bearer_token || "",
    access_token_ref: c.access_token_ref,
    access_token: c.access_token || "",
    refresh_token_ref: c.refresh_token_ref,
    refresh_token: c.refresh_token || "",
    stream_enabled: c.stream_enabled || false,
    stream_rules: c.stream_rules || [],
    require_publish_confirmation: c.require_publish_confirmation !== false,
    allow_actions: c.allow_actions || false,
  };
}

function _bindConnectorActions(id) {
  const saveId = `btn-save-app-${id}`;
  const testId = `btn-test-app-${id}`;
  document.getElementById(saveId)?.addEventListener("click", () => {
    appConnectorsPanel.saveStatus = "Saving...";
    appConnectorsPanel.saveStatusType = "";
    _setModalStatus("app-connector-modal-save-status", "", appConnectorsPanel.saveStatus);
    saveSettings();
    appConnectorsPanel.saveStatus = "Save requested. Start a new chat after it completes.";
    appConnectorsPanel.saveStatusType = "ok";
    _setModalStatus("app-connector-modal-save-status", "ok", appConnectorsPanel.saveStatus);
    renderAppConnectorsPanel();
  });
  document.getElementById(testId)?.addEventListener("click", () => {
    appConnectorsPanel.testStatus = "Testing...";
    appConnectorsPanel.testStatusType = "";
    _setModalStatus("app-connector-modal-test-status", "", appConnectorsPanel.testStatus);
    send(_testPayload(id));
  });
  document.getElementById(`btn-oauth-app-${id}`)?.addEventListener("click", () => _startOAuth(id));
}

function _openConnectorModal(id) {
  const item = _connectorById(id);
  appConnectorsPanel.selected = item.id;
  appConnectorsPanel.saveStatus = "";
  appConnectorsPanel.testStatus = "";
  openDialog({
    title: `Configure ${item.name}`,
    html: item.id === "github"
      ? _renderGitHubConfigModal()
      : item.id === "google-workspace"
        ? _renderGoogleWorkspaceConfigModal(item)
        : item.id === "notion"
          ? _renderNotionConfigModal(item)
          : item.id === "jira"
            ? _renderJiraConfigModal(item)
            : item.id === "reddit"
              ? _renderRedditConfigModal(item)
              : _renderXConfigModal(item),
    closeOnBackdrop: true,
    actions: [
      { label: "Close", secondary: true, onClick: closeModal },
    ],
  });
  if (item.id === "github") _bindGitHubConfig();
  else _bindConnectorActions(item.id);
}

export function renderAppConnectorsPanel() {
  const root = document.getElementById("app-connectors-content");
  if (!root) return;
  root.innerHTML = `
    <div class="app-connectors-header">
      <div>
        <div class="app-connectors-eyebrow">Built-in external app tools</div>
        <h1>App Connectors</h1>
        <p>Connect supported external services through HushClaw-provided adapters. Cards are not user-created plugins; they are product connectors that register tools into new chat sessions.</p>
      </div>
      <button id="btn-refresh-app-connectors" class="secondary">Refresh status</button>
    </div>
    ${_renderGroupedCards()}
  `;

  root.querySelectorAll("[data-app-connector]").forEach((card) => {
    card.addEventListener("click", () => {
      _openConnectorModal(card.dataset.appConnector || "github");
    });
  });
  document.getElementById("btn-refresh-app-connectors")?.addEventListener("click", () => {
    send({ type: "get_config_status" });
    refreshAppInbox();
  });
  refreshAppInbox();
}

export function handleTestAppConnectorResult(data) {
  appConnectorsPanel.testStatus = data.ok ? `Connected. ${data.message || ""}` : `Failed. ${data.message || ""}`;
  appConnectorsPanel.testStatusType = data.ok ? "ok" : "err";
  _setModalStatus("app-connector-modal-test-status", appConnectorsPanel.testStatusType, appConnectorsPanel.testStatus);
  renderAppConnectorsPanel();
}

function _renderInboxItem(item) {
  const payload = item.payload || {};
  const isDraft = String(item.event_type || "").startsWith("draft.");
  const isPublishing = appConnectorsPanel.publishingEventId === String(item.event_id || "");
  const publishBlockedReason = _publishBlockedReason();
  const publishDisabled = Boolean(isPublishing || publishBlockedReason);
  return `
    <div class="app-connector-planned-card" data-app-inbox-event="${escHtml(item.event_id || "")}">
      <div class="app-connector-planned-name">${escHtml(item.title || item.event_type || "Inbox event")}</div>
      <div class="app-connector-planned-note">${escHtml(item.body || item.source_url || "")}</div>
      <div class="app-connector-chips">
        <span>${escHtml(item.status || "unread")}</span>
        <span>${escHtml(item.event_type || "")}</span>
        ${payload.action ? `<span>${escHtml(payload.action)}</span>` : ""}
      </div>
      <div class="app-connector-actions compact">
        ${isDraft && item.status === "pending" ? `<button class="app-inbox-publish" data-event-id="${escHtml(item.event_id)}" data-blocked-reason="${escHtml(publishBlockedReason)}" ${publishDisabled ? "disabled" : ""}>${isPublishing ? "Publishing..." : "Publish"}</button>` : ""}
        <button class="secondary app-inbox-read" data-event-id="${escHtml(item.event_id)}">Mark read</button>
        <button class="secondary app-inbox-archive" data-event-id="${escHtml(item.event_id)}">Archive</button>
      </div>
      ${publishBlockedReason && isDraft && item.status === "pending" ? `<div class="app-connector-planned-note">${escHtml(publishBlockedReason)}</div>` : ""}
    </div>
  `;
}

function _publishBlockedReason() {
  const x = appConnectors.x || {};
  if (!x.enabled) return "Enable the X connector before publishing.";
  if (!x.access_token_set && !x.access_token) return "Connect X user OAuth before publishing.";
  if (!x.allow_actions) return "Enable post/reply actions before publishing.";
  return "";
}

function _bindInboxActions(root) {
  root.querySelectorAll(".app-inbox-read").forEach((btn) => {
    btn.addEventListener("click", () => send({ type: "update_app_inbox_event", event_id: btn.dataset.eventId, status: "read" }));
  });
  root.querySelectorAll(".app-inbox-archive").forEach((btn) => {
    btn.addEventListener("click", () => send({ type: "update_app_inbox_event", event_id: btn.dataset.eventId, status: "archived" }));
  });
  root.querySelectorAll(".app-inbox-publish").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (appConnectorsPanel.publishingEventId) return;
      appConnectorsPanel.publishingEventId = btn.dataset.eventId || "";
      appConnectorsPanel.inboxStatus = "Publishing draft to X...";
      appConnectorsPanel.inboxStatusType = "";
      btn.disabled = true;
      btn.textContent = "Publishing...";
      _setAppInboxStatus(root);
      send({ type: "publish_app_connector_draft", connector_id: "x", event_id: btn.dataset.eventId });
    });
  });
}

function _setAppInboxStatus(root) {
  const el = root?.querySelector("#app-connector-inbox-status");
  if (!el) return;
  el.innerHTML = _statusText(appConnectorsPanel.inboxStatusType, appConnectorsPanel.inboxStatus);
}

export function handleAppInboxEvents(data) {
  const root = document.getElementById("app-connectors-content");
  if (!root) return;
  let box = root.querySelector("#app-connector-inbox");
  if (!box) {
    root.insertAdjacentHTML("afterbegin", `<section id="app-connector-inbox" class="app-connectors-group"></section>`);
    box = root.querySelector("#app-connector-inbox");
  }
  const items = Array.isArray(data.items) ? data.items : [];
  box.innerHTML = `
    <div class="app-connectors-group-head">
      <h2>App Inbox</h2>
      <span>${items.length} event${items.length === 1 ? "" : "s"}</span>
    </div>
    <div id="app-connector-inbox-status">${_statusText(appConnectorsPanel.inboxStatusType, appConnectorsPanel.inboxStatus)}</div>
    <div class="app-connectors-planned-grid">
      ${items.length ? items.map(_renderInboxItem).join("") : `<div class="app-connector-planned-card"><div class="app-connector-planned-note">No app inbox events.</div></div>`}
    </div>
  `;
  _bindInboxActions(box);
}

export function refreshAppInbox(connectorId = "") {
  send({ type: "list_app_inbox_events", connector_id: connectorId, limit: 20 });
}

export function handleAppInboxEventUpdated() {
  refreshAppInbox();
}

export function handleAppConnectorDraftPublishProgress(data) {
  appConnectorsPanel.publishingEventId = data.event_id || appConnectorsPanel.publishingEventId;
  appConnectorsPanel.inboxStatus = data.message || "Publishing draft...";
  appConnectorsPanel.inboxStatusType = "";
  const root = document.getElementById("app-connectors-content");
  if (root) _setAppInboxStatus(root);
}

export function handleAppConnectorDraftPublished(data) {
  appConnectorsPanel.publishingEventId = "";
  appConnectorsPanel.inboxStatus = data.ok ? (data.message || "Draft published.") : `Failed. ${data.message || "Draft publish failed."}`;
  appConnectorsPanel.inboxStatusType = data.ok ? "ok" : "err";
  appConnectorsPanel.testStatus = appConnectorsPanel.inboxStatus;
  appConnectorsPanel.testStatusType = appConnectorsPanel.inboxStatusType;
  _setModalStatus("app-connector-modal-test-status", appConnectorsPanel.testStatusType, appConnectorsPanel.testStatus);
  refreshAppInbox();
}
