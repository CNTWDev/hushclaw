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
    name: "Google Workspace",
    icon: "GW",
    tagline: "Built-in Google adapter planned for Drive, Gmail, Calendar, and Docs.",
    capabilities: ["Drive", "Gmail", "Calendar", "Docs"],
    runtime: "OAuth app connector adapter",
    auth: "Google OAuth",
    planned: true,
  },
  {
    id: "notion",
    name: "Notion",
    icon: "NT",
    tagline: "Built-in Notion adapter planned for pages, databases, and team knowledge.",
    capabilities: ["Pages", "Databases", "Search"],
    runtime: "Notion API adapter",
    auth: "OAuth or integration token",
    planned: true,
  },
  {
    id: "jira",
    name: "Jira",
    icon: "JR",
    tagline: "Built-in Jira adapter planned for issue search, reading, and project context.",
    capabilities: ["Issues", "Projects", "Search"],
    runtime: "Jira Cloud REST adapter",
    auth: "OAuth or API token",
    planned: true,
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
    const cfg = appConnectors[item.id] || {};
    const status = item.planned ? "Built-in planned" : item.statusLabel(cfg);
    const statusClass = item.planned ? "off" : item.statusClass(cfg);
    const repo = cfg.default_repo ? escHtml(cfg.default_repo) : "No default repo";
    return `
      <button class="app-connector-card${item.planned ? " planned" : ""}"
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
          <span>${item.planned ? "View adapter details" : "Configure connection"}</span>
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
          <span>Default repository</span>
          <input id="app-github-default-repo" type="text" value="${escHtml(gh.default_repo || "")}" placeholder="owner/repo">
        </label>
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

function _renderPlannedConnectorModal(item) {
  return `
    <div class="app-connector-modal">
      <div class="app-connector-modal-summary">
        <div>
          <div class="app-connector-kicker">Built-in connector</div>
          <h2>${escHtml(item.name)}</h2>
          <p>${escHtml(item.tagline)}</p>
        </div>
        <span class="app-connector-status off">Not available yet</span>
      </div>

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
          <strong>Provided by HushClaw, not user-created</strong>
        </div>
      </div>

      <div class="app-connector-roadmap">
        <div class="app-connector-roadmap-title">Planned capabilities</div>
        <div class="app-connector-chips">
          ${item.capabilities.map((cap) => `<span>${escHtml(cap)}</span>`).join("")}
        </div>
        <p>
          This card is a built-in connector placeholder. When its adapter is implemented,
          this same panel will expose the account connection flow, status, and tool registration controls.
        </p>
      </div>
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

function _openConnectorModal(id) {
  const item = _connectorById(id);
  appConnectorsPanel.selected = item.id;
  appConnectorsPanel.saveStatus = "";
  appConnectorsPanel.testStatus = "";
  openDialog({
    title: item.planned ? `${item.name} details` : `Configure ${item.name}`,
    html: item.id === "github" ? _renderGitHubConfigModal() : _renderPlannedConnectorModal(item),
    closeOnBackdrop: true,
    actions: [
      { label: "Close", secondary: true, onClick: closeModal },
    ],
  });
  if (item.id === "github") _bindGitHubConfig();
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
  if (data.target !== "github") return;
  appConnectorsPanel.testStatus = data.ok ? `Connected. ${data.message || ""}` : `Failed. ${data.message || ""}`;
  appConnectorsPanel.testStatusType = data.ok ? "ok" : "err";
  _setModalStatus("app-connector-modal-test-status", appConnectorsPanel.testStatusType, appConnectorsPanel.testStatus);
  renderAppConnectorsPanel();
}
