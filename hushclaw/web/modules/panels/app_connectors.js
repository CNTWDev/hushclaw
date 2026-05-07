/**
 * panels/app_connectors.js — Main App Connectors panel.
 */

import {
  appConnectors, appConnectorsPanel, els, escHtml, send,
} from "../state.js";
import { syncFormToState, saveSettings } from "../settings/save.js";

const CONNECTORS = [
  {
    id: "github",
    name: "GitHub",
    icon: "GH",
    tagline: "Search and read issues, pull requests, code, commits, and repositories.",
    capabilities: ["Search", "Read", "Sources"],
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
    tagline: "Drive, Gmail, Calendar, and Docs connector planned for the same runtime.",
    capabilities: ["Planned", "OAuth", "Read"],
    disabled: true,
  },
  {
    id: "notion",
    name: "Notion",
    icon: "NT",
    tagline: "Workspace pages and databases connector planned after GitHub v1.",
    capabilities: ["Planned", "Read"],
    disabled: true,
  },
  {
    id: "jira",
    name: "Jira",
    icon: "JR",
    tagline: "Issue search/read connector planned for product and engineering workflows.",
    capabilities: ["Planned", "Search"],
    disabled: true,
  },
];

function _statusText(type, text) {
  if (!text) return "";
  return `<span class="app-connector-inline-status ${type || ""}">${escHtml(text)}</span>`;
}

function _renderCards() {
  return CONNECTORS.map((item) => {
    const cfg = appConnectors[item.id] || {};
    const selected = appConnectorsPanel.selected === item.id;
    const status = item.disabled ? "Coming later" : item.statusLabel(cfg);
    const statusClass = item.disabled ? "off" : item.statusClass(cfg);
    const repo = cfg.default_repo ? `<span>${escHtml(cfg.default_repo)}</span>` : "";
    return `
      <button class="app-connector-card${selected ? " active" : ""}${item.disabled ? " disabled" : ""}"
              data-app-connector="${escHtml(item.id)}"
              ${item.disabled ? "disabled" : ""}>
        <div class="app-connector-card-top">
          <span class="app-connector-mark">${escHtml(item.icon)}</span>
          <span class="app-connector-status ${statusClass}">${escHtml(status)}</span>
        </div>
        <div class="app-connector-card-name">${escHtml(item.name)}</div>
        <div class="app-connector-card-desc">${escHtml(item.tagline)}</div>
        <div class="app-connector-chips">
          ${item.capabilities.map((cap) => `<span>${escHtml(cap)}</span>`).join("")}
        </div>
        ${repo ? `<div class="app-connector-card-meta">${repo}</div>` : ""}
      </button>
    `;
  }).join("");
}

function _renderGitHubConfig() {
  const gh = appConnectors.github;
  const tokenPlaceholder = gh.token_set ? "Token already set; leave blank to keep it" : "GitHub fine-grained token";
  const tokenState = gh.token_set ? "Token stored outside hushclaw.toml" : "No token stored yet";

  return `
    <section class="app-connector-config-card">
      <div class="app-connector-config-head">
        <div>
          <div class="app-connector-kicker">GitHub connector</div>
          <h2>Repository search and read tools</h2>
          <p>Once connected, new chat sessions can use GitHub tools when the model decides repository context is useful.</p>
        </div>
        <label class="toggle">
          <input type="checkbox" id="app-github-enabled" ${gh.enabled ? "checked" : ""}>
          <span class="slider"></span>
        </label>
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
        ${_statusText(appConnectorsPanel.saveStatusType, appConnectorsPanel.saveStatus)}
        ${_statusText(appConnectorsPanel.testStatusType, appConnectorsPanel.testStatus)}
      </div>
    </section>
  `;
}

function _bindGitHubConfig() {
  document.getElementById("btn-save-app-github")?.addEventListener("click", () => {
    appConnectorsPanel.saveStatus = "Saving...";
    appConnectorsPanel.saveStatusType = "";
    renderAppConnectorsPanel();
    saveSettings();
    appConnectorsPanel.saveStatus = "Save requested. Start a new chat after it completes.";
    appConnectorsPanel.saveStatusType = "ok";
    renderAppConnectorsPanel();
  });

  document.getElementById("btn-test-app-github")?.addEventListener("click", () => {
    syncFormToState();
    appConnectorsPanel.testStatus = "Testing...";
    appConnectorsPanel.testStatusType = "";
    renderAppConnectorsPanel();
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

export function renderAppConnectorsPanel() {
  const root = document.getElementById("app-connectors-content");
  if (!root) return;
  root.innerHTML = `
    <div class="app-connectors-header">
      <div>
        <div class="app-connectors-eyebrow">External app tools</div>
        <h1>App Connectors</h1>
        <p>Connect third-party workspaces as discoverable tools for new chat sessions.</p>
      </div>
      <button id="btn-refresh-app-connectors" class="secondary">Refresh status</button>
    </div>
    <div class="app-connectors-grid">
      ${_renderCards()}
    </div>
    ${appConnectorsPanel.selected === "github" ? _renderGitHubConfig() : ""}
  `;

  root.querySelectorAll("[data-app-connector]").forEach((card) => {
    card.addEventListener("click", () => {
      appConnectorsPanel.selected = card.dataset.appConnector || "github";
      appConnectorsPanel.saveStatus = "";
      appConnectorsPanel.testStatus = "";
      renderAppConnectorsPanel();
    });
  });
  document.getElementById("btn-refresh-app-connectors")?.addEventListener("click", () => {
    send({ type: "get_config_status" });
  });
  _bindGitHubConfig();
}

export function handleTestAppConnectorResult(data) {
  if (data.target !== "github") return;
  appConnectorsPanel.testStatus = data.ok ? `Connected. ${data.message || ""}` : `Failed. ${data.message || ""}`;
  appConnectorsPanel.testStatusType = data.ok ? "ok" : "err";
  renderAppConnectorsPanel();
}
