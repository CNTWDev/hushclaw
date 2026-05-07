/**
 * panels/app_connectors.js — Main App Connectors panel.
 */

import {
  appConnectors, appConnectorsPanel, els, escHtml, send,
} from "../state.js";
import { syncFormToState, saveSettings } from "../settings/save.js";
import { openDialog, closeModal } from "../modal.js";

const CONNECTORS = [
  {
    id: "github",
    name: "GitHub",
    icon: "GH",
    tagline: "Built-in repository connector for issues, pull requests, code, commits, and repositories.",
    capabilities: ["Search", "Read", "Sources"],
    runtime: "Read-only REST adapter",
    auth: "Fine-grained access token",
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
    id: "google-workspace",
    stateKey: "google_workspace",
    name: "Google Workspace",
    icon: "GW",
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
    icon: "NT",
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
    icon: "JR",
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

function _connectorById(id) {
  return CONNECTORS.find((item) => item.id === id) || CONNECTORS[0];
}

function _statusText(type, text) {
  if (!text) return "";
  return `<span class="app-connector-inline-status ${type || ""}">${escHtml(text)}</span>`;
}

function _renderCards() {
  return CONNECTORS.map((item) => {
    const stateKey = item.stateKey || item.id;
    const cfg = appConnectors[stateKey] || {};
    const status = item.statusLabel ? item.statusLabel(cfg) : "Not connected";
    const statusClass = item.statusClass ? item.statusClass(cfg) : "off";
    const repo = cfg.default_repo ? escHtml(cfg.default_repo) : "No default repo";
    return `
      <button class="app-connector-card"
              data-app-connector="${escHtml(item.id)}">
        <div class="app-connector-card-top">
          <span class="app-connector-mark" aria-hidden="true">${escHtml(item.icon)}</span>
          <div class="app-connector-title-block">
            <span class="app-connector-card-type">Built-in connector</span>
            <span class="app-connector-card-name">${escHtml(item.name)}</span>
          </div>
        </div>
        <span class="app-connector-status ${statusClass}">${escHtml(status)}</span>
        <div class="app-connector-card-desc">${escHtml(item.tagline)}</div>
        <div class="app-connector-chips">
          ${item.capabilities.map((cap) => `<span>${escHtml(cap)}</span>`).join("")}
        </div>
        <div class="app-connector-card-meta-grid">
          <span><b>Auth</b>${escHtml(item.auth)}</span>
          <span><b>Runtime</b>${escHtml(item.runtime)}</span>
          ${item.id === "github" ? `<span><b>Scope</b>${repo}</span>` : ""}
        </div>
        <div class="app-connector-card-footer">
          <span>Configure connection</span>
          <span aria-hidden="true">→</span>
        </div>
      </button>
    `;
  }).join("");
}

function _renderGitHubConfigModal() {
  const gh = appConnectors.github;
  const tokenPlaceholder = gh.token_set ? "Token already set; leave blank to keep it" : "GitHub fine-grained token";
  const tokenState = gh.token_set ? "Token stored outside hushclaw.toml" : "No token stored yet";

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

      <div class="app-connector-form-grid">
        <label class="settings-field">
          <span>Auth type</span>
          <select id="app-github-auth-type">
            <option value="pat" ${gh.auth_type === "pat" ? "selected" : ""}>Fine-grained token</option>
          </select>
        </label>
        <label class="settings-field">
          <span>Default repository</span>
          <input id="app-github-default-repo" type="text" value="${escHtml(gh.default_repo || "")}" placeholder="owner/repo">
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

      <div class="app-connector-actions">
        <button id="btn-save-app-github">Save connector</button>
        <button id="btn-test-app-github" class="secondary">Test connection</button>
        <span id="app-connector-modal-save-status">${_statusText(appConnectorsPanel.saveStatusType, appConnectorsPanel.saveStatus)}</span>
        <span id="app-connector-modal-test-status">${_statusText(appConnectorsPanel.testStatusType, appConnectorsPanel.testStatus)}</span>
      </div>
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
  return `
    <div class="app-connector-modal">
      ${_commonModalSummary(item, "app-google-workspace-enabled", c.enabled)}
      ${_commonInfoGrid(item, "Google SDK adapter with OAuth credentials")}

      <div class="app-connector-form-grid">
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

      ${_renderConnectorActions("google-workspace")}
    </div>
  `;
}

function _renderNotionConfigModal(item) {
  const c = appConnectors.notion;
  return `
    <div class="app-connector-modal">
      ${_commonModalSummary(item, "app-notion-enabled", c.enabled)}
      ${_commonInfoGrid(item, "Notion SDK adapter with workspace token")}

      <div class="app-connector-form-grid">
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
          <span>Notion token</span>
          <input id="app-notion-token" type="password" value="" placeholder="${escHtml(_secretPlaceholder(c.token_set, "Notion integration token"))}">
        </label>
        <label class="settings-field">
          <span>Token secret reference</span>
          <input id="app-notion-token-ref" type="text" value="${escHtml(c.token_ref)}">
        </label>
      </div>

      ${_renderConnectorActions("notion")}
    </div>
  `;
}

function _renderJiraConfigModal(item) {
  const c = appConnectors.jira;
  return `
    <div class="app-connector-modal">
      ${_commonModalSummary(item, "app-jira-enabled", c.enabled)}
      ${_commonInfoGrid(item, "Jira Cloud REST adapter")}

      <div class="app-connector-form-grid">
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

      ${_renderConnectorActions("jira")}
    </div>
  `;
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
      token_ref: appConnectors.github.token_ref || "app_connectors.github.token",
      token: appConnectors.github.token || "",
      default_repo: appConnectors.github.default_repo || "",
      allow_actions: false,
    });
  });
}

function _testPayload(id) {
  syncFormToState();
  if (id === "github") {
    return {
      type: "test_app_connector",
      target: "github",
      enabled: appConnectors.github.enabled,
      auth_type: appConnectors.github.auth_type || "pat",
      token_ref: appConnectors.github.token_ref || "app_connectors.github.token",
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
      auth_type: c.auth_type || "internal_token",
      token_ref: c.token_ref,
      token: c.token || "",
      workspace_name: c.workspace_name || "",
      allow_actions: false,
    };
  }
  const c = appConnectors.jira;
  return {
    type: "test_app_connector",
    target: "jira",
    enabled: c.enabled,
    auth_type: c.auth_type || "api_token",
    site_url: c.site_url || "",
    email: c.email || "",
    token_ref: c.token_ref,
    token: c.token || "",
    access_token_ref: c.access_token_ref,
    access_token: c.access_token || "",
    cloud_id: c.cloud_id || "",
    allow_actions: false,
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
          : _renderJiraConfigModal(item),
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
    <div class="app-connectors-grid">
      ${_renderCards()}
    </div>
  `;

  root.querySelectorAll("[data-app-connector]").forEach((card) => {
    card.addEventListener("click", () => {
      _openConnectorModal(card.dataset.appConnector || "github");
    });
  });
  document.getElementById("btn-refresh-app-connectors")?.addEventListener("click", () => {
    send({ type: "get_config_status" });
  });
}

export function handleTestAppConnectorResult(data) {
  appConnectorsPanel.testStatus = data.ok ? `Connected. ${data.message || ""}` : `Failed. ${data.message || ""}`;
  appConnectorsPanel.testStatusType = data.ok ? "ok" : "err";
  _setModalStatus("app-connector-modal-test-status", appConnectorsPanel.testStatusType, appConnectorsPanel.testStatus);
  renderAppConnectorsPanel();
}
