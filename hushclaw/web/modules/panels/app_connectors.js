/**
 * panels/app_connectors.js — Unified Connections panel.
 */

import {
  appConnectors, appConnectorsPanel, connectionsView, connectors, wizard, els, escHtml, send,
} from "../state.js";
import { syncFormToState, saveSettings } from "../settings/save.js";
import { openDialog, closeModal } from "../modal.js";
import { withApiKey } from "../http.js";
import { CHANNELS } from "../settings/providers.js";

const CONNECTORS = [
  {
    id: "github",
    name: "GitHub",
    category: "Developer",
    icon: "github",
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
    icon: "reddit",
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
    icon: "x",
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
    id: "jira",
    name: "Jira",
    category: "Developer",
    icon: "jira",
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

const CATEGORY_ORDER = ["Developer", "Social / Content Platforms"];
const CONNECTION_KIND_ORDER = ["app", "channel", "sync_source"];
const CONNECTION_KIND_LABELS = {
  app: "Apps",
  channel: "Channels",
  sync_source: "Sync Sources",
};
const CONNECTION_KIND_DESCRIPTIONS = {
  app: "External app tools that extend the agent runtime.",
  channel: "Inbound and outbound messaging channels connected to agents.",
  sync_source: "External inboxes and calendars that sync into local product features.",
};
const APP_PANEL_IDS = new Set(["github", "google_workspace", "notion", "jira", "reddit", "x"]);
const CONNECTION_BRANDS = {
  google_workspace: "google-workspace",
  email: "email",
  calendar: "calendar",
  telegram: "telegram",
  feishu: "feishu",
  slack: "slack",
  discord: "discord",
  dingtalk: "dingtalk",
  wecom: "wecom",
};

const PLANNED_CONNECTORS = [];

function _connectorById(id) {
  return CONNECTORS.find((item) => item.id === id) || CONNECTORS[0];
}

function _connectionKindLabel(kind) {
  return CONNECTION_KIND_LABELS[kind] || "Connections";
}

function _normalizeConnectionItem(item) {
  if (item && item.kind) {
    return {
      ...item,
      brand: item.brand || CONNECTION_BRANDS[item.provider] || item.provider || item.id,
      category: _connectionKindLabel(item.kind),
      capabilities: Array.isArray(item.capabilities) ? item.capabilities : [],
      manage_target: item.manage_target || "settings",
      manage_id: item.manage_id || item.id,
      meta: item.meta || {},
    };
  }
  const fallback = _connectorById(item?.id || "github");
  return {
    id: fallback.id,
    kind: "app",
    provider: fallback.id,
    name: fallback.name,
    description: fallback.tagline,
    capabilities: fallback.capabilities || [],
    enabled: false,
    configured: false,
    connected: false,
    state: "disabled",
    auth: fallback.auth,
    brand: fallback.brand || fallback.id,
    category: "Apps",
    manage_target: "panel",
    manage_id: fallback.id,
    meta: {},
  };
}

function _connectionsDirectoryItems() {
  const items = Array.isArray(connectionsView.items) ? connectionsView.items : [];
  if (items.length) return items.map(_normalizeConnectionItem);
  return CONNECTORS.map((item) => _normalizeConnectionItem({
    id: item.id,
    kind: "app",
    provider: item.id,
    name: item.name,
    description: item.tagline,
    capabilities: item.capabilities,
    enabled: false,
    configured: false,
    connected: false,
    state: "disabled",
    auth: item.auth,
    brand: item.brand || item.id,
    manage_target: "panel",
    manage_id: item.id,
  }));
}

function _connectionStateInfo(item) {
  const state = String(item?.state || "disabled");
  if (state === "connected") return { label: "Connected", className: "ok" };
  if (state === "enabled") return { label: "Enabled", className: "ok" };
  if (state === "configured") return { label: "Configured", className: "warn" };
  if (state === "needs_config") return { label: "Needs setup", className: "warn" };
  return { label: item?.configured ? "Disabled" : "Not connected", className: "off" };
}

function _statusText(type, text) {
  if (!text) return "";
  return `<span class="app-connector-inline-status ${type || ""}">${escHtml(text)}</span>`;
}

function _fv(id) {
  const el = document.getElementById(id);
  return el ? el.value.trim() : "";
}

function _fc(id, fallback = false) {
  const el = document.getElementById(id);
  return el ? el.checked : fallback;
}

function _intList(raw) {
  return (raw || "").split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => !Number.isNaN(n));
}

function _strList(raw) {
  return (raw || "").split(",").map((s) => s.trim()).filter(Boolean);
}

function _connectorIcon(name) {
  const key = String(name || "").toLowerCase();
  const icons = {
    github: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 .5a12 12 0 0 0-3.79 23.39c.6.11.82-.26.82-.58v-2.03c-3.34.73-4.04-1.42-4.04-1.42-.55-1.39-1.34-1.76-1.34-1.76-1.09-.75.08-.73.08-.73 1.2.08 1.84 1.24 1.84 1.24 1.07 1.83 2.81 1.3 3.5.99.11-.78.42-1.3.76-1.6-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.12-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.66.24 2.88.12 3.18.77.84 1.23 1.91 1.23 3.22 0 4.61-2.81 5.63-5.49 5.93.43.37.81 1.1.81 2.22v3.29c0 .32.22.7.83.58A12 12 0 0 0 12 .5Z"/></svg>`,
    jira: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M11.53 2.3 2.3 11.53a1.6 1.6 0 0 0 0 2.26l7.21 7.21 4.17-4.17-4.38-4.38 6.4-6.4-4.17-3.75Z"/><path fill="currentColor" d="m14.49 3 4.17 4.17-6.39 6.39 4.38 4.38-4.18 4.18 9.23-9.23a1.6 1.6 0 0 0 0-2.26L14.49 3Z" opacity=".62"/></svg>`,
    reddit: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M21.5 11.9a2.6 2.6 0 0 0-4.4-1.85 12.9 12.9 0 0 0-4.01-1.13l.68-3.2 2.24.48a1.9 1.9 0 1 0 .24-1.12l-2.8-.6a.58.58 0 0 0-.69.45l-.85 3.98a13.2 13.2 0 0 0-5.05 1.14A2.6 2.6 0 1 0 4 14.29c-.02.18-.03.36-.03.55 0 3.28 3.6 5.94 8.03 5.94s8.03-2.66 8.03-5.94c0-.18 0-.36-.03-.54a2.6 2.6 0 0 0 1.5-2.4ZM8.77 13.82a1.45 1.45 0 1 1 0 2.9 1.45 1.45 0 0 1 0-2.9Zm6.67 4.02c-.98.98-2.84 1.06-3.44 1.06-.6 0-2.46-.08-3.44-1.06a.57.57 0 0 1 .8-.8c.62.62 1.95.73 2.64.73.69 0 2.02-.11 2.64-.73a.57.57 0 0 1 .8.8Zm-.2-1.12a1.45 1.45 0 1 1 0-2.9 1.45 1.45 0 0 1 0 2.9Z"/></svg>`,
    x: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M18.9 2h3.2l-7 8.01L23.34 22h-6.45l-5.05-6.6L6.06 22h-3.2l7.49-8.56L2.45 2h6.61l4.56 6.03L18.9 2Zm-1.12 17.89h1.77L8.1 4H6.2l11.58 15.89Z"/></svg>`,
    google_workspace: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" d="M22 12.27c0-.82-.07-1.49-.22-2.18H12v4.13h5.75c-.12 1.03-.78 2.58-2.25 3.62l-.02.14 3.27 2.49.23.02C20.86 18.78 22 15.83 22 12.27Z"/><path fill="#34A853" d="M12 22c2.81 0 5.17-.91 6.9-2.48l-3.29-2.65c-.88.61-2.05 1.03-3.61 1.03-2.75 0-5.09-1.78-5.92-4.24l-.14.01-3.4 2.58-.05.13C4.21 19.74 7.83 22 12 22Z"/><path fill="#FBBC05" d="M6.08 13.66A5.85 5.85 0 0 1 5.74 12c0-.58.12-1.13.32-1.66l-.01-.11-3.45-2.62-.11.05A9.83 9.83 0 0 0 2 12c0 1.57.38 3.06 1.04 4.34l3.04-2.68Z"/><path fill="#EA4335" d="M12 6.1c1.96 0 3.28.83 4.03 1.53l2.94-2.8C17.16 3.26 14.8 2 12 2 7.83 2 4.21 4.26 2.48 7.66l3.57 2.68C6.91 7.88 9.25 6.1 12 6.1Z"/></svg>`,
    notion: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M4.4 4.7 11 4l8.4.58v14.69l-8.16.73L4.4 18.86Zm1.45 1.35v11.53l4.39.54V9.9l.06-.02 4.95 8.87 2.7.16V6.07l-4.33-.32v8.17l-.06.01L8.62 5.97Z"/></svg>`,
    email: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3 5.5h18A1.5 1.5 0 0 1 22.5 7v10A1.5 1.5 0 0 1 21 18.5H3A1.5 1.5 0 0 1 1.5 17V7A1.5 1.5 0 0 1 3 5.5Zm0 1.8v.19l9 5.71 9-5.71V7.3H3Zm18 9.4V9.58l-8.52 5.4a.9.9 0 0 1-.96 0L3 9.58v7.12h18Z"/></svg>`,
    calendar: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M7 2.5a.75.75 0 0 1 .75.75V5h8.5V3.25a.75.75 0 0 1 1.5 0V5H19a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h1.25V3.25A.75.75 0 0 1 7 2.5ZM4.5 9v9a.5.5 0 0 0 .5.5h14a.5.5 0 0 0 .5-.5V9h-15Zm3 2.25h3v3h-3Zm4.5 0h3v3h-3Z"/></svg>`,
    telegram: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M21.9 4.6c.3-.95-.63-1.82-1.53-1.45L3.42 9.7c-1 .38-.95 1.82.08 2.12l4.3 1.24 1.64 5.17c.32 1 .6 1.24 1.17 1.24.56 0 .81-.21 1.13-.52l2.38-2.32 4.95 3.66c.91.67 1.56.32 1.8-.84L21.9 4.6Zm-12.62 8.1 8.42-5.31c.42-.26.8.09.48.38l-6.95 6.27-.27 2.88-1.68-4.22Z"/></svg>`,
    feishu: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#00C2FF" d="M6.2 3.5h7.2a3.3 3.3 0 0 1 3.3 3.3v7.05a3.3 3.3 0 0 1-3.3 3.3H6.35a3.35 3.35 0 0 1 0-6.7h5.3a1.2 1.2 0 1 0 0-2.4H6.2Z"/><path fill="#3370FF" d="M17.8 20.5h-7.2a3.3 3.3 0 0 1-3.3-3.3v-7.05a3.3 3.3 0 0 1 3.3-3.3h7.05a3.35 3.35 0 1 1 0 6.7h-5.3a1.2 1.2 0 1 0 0 2.4h5.45Z"/></svg>`,
    slack: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#E01E5A" d="M10.2 2a2.2 2.2 0 1 0 0 4.4h2.2V4.2A2.2 2.2 0 0 0 10.2 2Zm0 5.7H4.7a2.2 2.2 0 1 0 0 4.4h5.5a2.2 2.2 0 0 0 0-4.4Z"/><path fill="#36C5F0" d="M22 10.2a2.2 2.2 0 1 0-4.4 0v2.2h2.2A2.2 2.2 0 0 0 22 10.2Zm-5.7 0V4.7a2.2 2.2 0 1 0-4.4 0v5.5a2.2 2.2 0 0 0 4.4 0Z"/><path fill="#2EB67D" d="M13.8 22a2.2 2.2 0 1 0 0-4.4h-2.2v2.2a2.2 2.2 0 0 0 2.2 2.2Zm0-5.7h5.5a2.2 2.2 0 1 0 0-4.4h-5.5a2.2 2.2 0 0 0 0 4.4Z"/><path fill="#ECB22E" d="M2 13.8a2.2 2.2 0 1 0 4.4 0v-2.2H4.2A2.2 2.2 0 0 0 2 13.8Zm5.7 0v5.5a2.2 2.2 0 1 0 4.4 0v-5.5a2.2 2.2 0 0 0-4.4 0Z"/></svg>`,
    discord: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M19.54 5.34A17.1 17.1 0 0 0 15.4 4l-.2.42a15.8 15.8 0 0 1 3.96 1.37c-1.67-.84-3.47-1.47-5.32-1.79a17.8 17.8 0 0 0-3.68 0C8.3 4.32 6.5 4.95 4.84 5.79A15.8 15.8 0 0 1 8.8 4.42L8.6 4a17.1 17.1 0 0 0-4.14 1.34C1.84 9.18 1.13 12.9 1.5 16.57A17.3 17.3 0 0 0 6.58 19l1.1-1.53c-.58-.22-1.13-.49-1.65-.81.14.1.28.19.43.28 3.18 1.74 6.9 1.74 10.06 0 .15-.09.29-.18.43-.28-.52.32-1.07.59-1.65.81l1.1 1.53a17.3 17.3 0 0 0 5.08-2.43c.43-4.2-.73-7.88-2.94-11.23ZM9.24 14.32c-.98 0-1.78-.9-1.78-2.01s.79-2.01 1.78-2.01c1 0 1.8.9 1.78 2.01 0 1.11-.79 2.01-1.78 2.01Zm5.52 0c-.98 0-1.78-.9-1.78-2.01s.79-2.01 1.78-2.01c1 0 1.8.9 1.78 2.01 0 1.11-.79 2.01-1.78 2.01Z"/></svg>`,
    dingtalk: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M19.8 3.5c-.18 0-.35.05-.5.14L4.7 11.8c-.57.32-.53 1.16.07 1.42l3.96 1.7 1.35 4.1c.2.61.99.76 1.41.28l2.5-2.88 4.03 2.21c.62.34 1.37-.13 1.34-.83l-.57-13.45c-.02-.48-.41-.85-.99-.85Z"/></svg>`,
    wecom: `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#1AAD19" d="M9.48 4C5.35 4 2 6.8 2 10.24c0 1.96 1.1 3.7 2.8 4.84L4.1 18l2.72-1.5c.84.18 1.73.28 2.66.28 4.13 0 7.48-2.8 7.48-6.24S13.6 4 9.48 4Zm-2.7 5.32a.88.88 0 1 1 0-1.76.88.88 0 0 1 0 1.76Zm5.4 0a.88.88 0 1 1 0-1.76.88.88 0 0 1 0 1.76Z"/><path fill="#07C160" d="M17.54 8.53c-2.46 0-4.46 1.68-4.46 3.75 0 2.07 2 3.75 4.46 3.75.55 0 1.07-.08 1.55-.22l2 1.08-.5-2.06c.88-.69 1.41-1.62 1.41-2.55 0-2.07-2-3.75-4.46-3.75Z"/></svg>`,
  };
  return icons[key] || escHtml(name || "");
}

function _renderGroupedCards() {
  const categories = new Map();
  _connectionsDirectoryItems().forEach((item) => {
    const category = item.category || _connectionKindLabel(item.kind);
    if (!categories.has(category)) categories.set(category, []);
    categories.get(category).push(item);
  });
  PLANNED_CONNECTORS.forEach((item) => {
    const category = item.category || "Other";
    if (!categories.has(category)) categories.set(category, []);
  });
  const ordered = [
    ...CONNECTION_KIND_ORDER.map((kind) => _connectionKindLabel(kind)).filter((cat) => categories.has(cat)),
    ...CATEGORY_ORDER.filter((cat) => categories.has(cat)),
    ...[...categories.keys()].filter((cat) => !CATEGORY_ORDER.includes(cat) && !Object.values(CONNECTION_KIND_LABELS).includes(cat)).sort(),
  ];
  return ordered.map((category) => `
    <section class="app-connectors-group">
      <div class="app-connectors-group-head">
        <h2>${escHtml(category)}</h2>
        <span>${escHtml(CONNECTION_KIND_DESCRIPTIONS[Object.keys(CONNECTION_KIND_LABELS).find((kind) => CONNECTION_KIND_LABELS[kind] === category)] || `${categories.get(category).length} configured connections`)}</span>
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
  const statusInfo = _connectionStateInfo(item);
  const kindLabel = item.kind === "sync_source" ? "Sync source" : item.kind === "channel" ? "Channel" : "App";
  const footerLabel = item.manage_target === "panel" ? "Open connection" : "View details";
  return `
    <button class="app-connector-card app-connector-card-${escHtml(item.brand || item.id)}"
            data-app-connector="${escHtml(item.id)}">
      <div class="app-connector-card-top">
        <span class="app-connector-mark" aria-hidden="true">${_connectorIcon(item.icon || item.provider || item.brand)}</span>
        <div class="app-connector-title-block">
          <span class="app-connector-card-type">${escHtml(kindLabel)}</span>
          <span class="app-connector-card-name">${escHtml(item.name)}</span>
        </div>
      </div>
      <div class="app-connector-status-row">
        <span class="app-connector-status ${escHtml(statusInfo.className)}">${escHtml(statusInfo.label)}</span>
        <span class="app-connector-kind-chip">${escHtml(item.provider.replaceAll("_", " "))}</span>
      </div>
      <div class="app-connector-card-desc">${escHtml(item.description || item.tagline || "")}</div>
      <div class="app-connector-chips">
        ${(item.capabilities || []).map((cap) => `<span>${escHtml(cap)}</span>`).join("")}
      </div>
      <div class="app-connector-card-footer">
        <span>${escHtml(footerLabel)}</span>
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
      ${_renderOAuthConnectBlock("google_workspace", oauthReady, c.refresh_token_set || c.access_token_set, "Connect Google Workspace")}

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

      ${_renderConnectorActions("google_workspace")}
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
        <div><span>Stream</span><strong>Filtered Stream → Local events</strong></div>
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

function _renderConnectionDetailsModal(item) {
  const statusInfo = _connectionStateInfo(item);
  const meta = item.meta || {};
  const metaRows = [];
  if (meta.workspace) metaRows.push(["Workspace", meta.workspace]);
  if (meta.agent) metaRows.push(["Agent", meta.agent]);
  if (meta.username) metaRows.push(["Username", meta.username]);
  if (meta.mailbox) metaRows.push(["Mailbox", meta.mailbox]);
  if (meta.url) metaRows.push(["Endpoint", meta.url]);
  if (meta.calendar_name) metaRows.push(["Calendar", meta.calendar_name]);
  return `
    <div class="app-connector-modal">
      <div class="app-connector-modal-summary">
        <div>
          <div class="app-connector-kicker">${escHtml(_connectionKindLabel(item.kind))}</div>
          <h2>${escHtml(item.name)}</h2>
          <p>${escHtml(item.description || "")}</p>
        </div>
        <span class="app-connector-status ${escHtml(statusInfo.className)}">${escHtml(statusInfo.label)}</span>
      </div>

      <div class="app-connector-info-grid">
        <div>
          <span>Kind</span>
          <strong>${escHtml(item.kind.replace("_", " "))}</strong>
        </div>
        <div>
          <span>Authentication</span>
          <strong>${escHtml(item.auth || "Configured in Settings")}</strong>
        </div>
        <div>
          <span>Capabilities</span>
          <strong>${escHtml((item.capabilities || []).join(", ") || "None")}</strong>
        </div>
      </div>

      ${metaRows.length ? `
        <div class="app-connector-info-grid">
          ${metaRows.slice(0, 3).map(([label, value]) => `
            <div>
              <span>${escHtml(label)}</span>
              <strong>${escHtml(String(value))}</strong>
            </div>
          `).join("")}
        </div>
      ` : ""}

      <div class="app-connector-roadmap">
        <div class="app-connector-roadmap-title">Management path</div>
        <p>Email and calendar sources are still managed from Integrations for now. The Connections directory keeps them visible alongside apps and channels while runtime/config ownership continues to migrate here.</p>
      </div>

      <div class="app-connector-actions">
        <button id="btn-open-connection-settings">Open Settings</button>
        <button id="btn-refresh-connections-modal" class="secondary">Refresh status</button>
      </div>
    </div>
  `;
}

function _openConnectionSettings(item) {
  import("../settings.js").then(({ openWizard }) => {
    wizard.tab = "integrations";
    openWizard(true);
    closeModal();
  });
  send({ type: "get_config_status" });
}

function _channelDefinition(provider) {
  return CHANNELS.find((ch) => ch.id === provider) || null;
}

function _renderChannelConfigModal(item) {
  const c = connectors[item.provider] || {};
  const channel = _channelDefinition(item.provider);
  const statusInfo = _connectionStateInfo(item);
  return `
    <div class="app-connector-modal">
      <div class="app-connector-modal-summary">
        <div>
          <div class="app-connector-kicker">Channel connection</div>
          <h2>${escHtml(item.name)}</h2>
          <p>${escHtml(item.description || "")}</p>
        </div>
        <span class="app-connector-status ${escHtml(statusInfo.className)}">${escHtml(statusInfo.label)}</span>
      </div>

      <div class="app-connector-info-grid">
        <div>
          <span>Provider</span>
          <strong>${escHtml(item.provider.replaceAll("_", " "))}</strong>
        </div>
        <div>
          <span>Capabilities</span>
          <strong>${escHtml((item.capabilities || []).join(", "))}</strong>
        </div>
        <div>
          <span>Lifecycle</span>
          <strong>${item.connected ? "Connected runtime" : item.enabled ? "Enabled / waiting" : "Disabled"}</strong>
        </div>
      </div>

      <div class="app-connector-roadmap">
        <div class="app-connector-roadmap-title">Connections-managed channel</div>
        <p>Channel credentials and routing stay inside this Connections module. No Settings or Wizard hand-off is required.</p>
      </div>

      <div class="app-connector-channel-form">
        ${channel ? channel.fields(c) : `<p class="settings-hint">No channel renderer available.</p>`}
      </div>

      <div class="app-connector-actions">
        <button id="btn-save-channel-${escHtml(item.provider)}">Save connection</button>
        <button id="btn-refresh-channel-${escHtml(item.provider)}" class="secondary">Refresh status</button>
        <span id="app-connector-modal-save-status">${_statusText(appConnectorsPanel.saveStatusType, appConnectorsPanel.saveStatus)}</span>
      </div>
    </div>
  `;
}

function _syncChannelFormToState(provider) {
  if (provider === "telegram") {
    const c = connectors.telegram;
    c.enabled = _fc("telegram-enabled", c.enabled);
    c.bot_token = _fv("tg-token");
    c.agent = _fv("tg-agent") || "default";
    c.workspace = _fv("tg-workspace");
    c.allowlist = _fv("tg-allowlist");
    c.group_allowlist = _fv("tg-group-allowlist");
    c.group_policy = _fv("tg-group-policy") || "allowlist";
    c.require_mention = _fc("tg-require-mention", c.require_mention);
    c.stream = _fc("tg-stream", c.stream);
    c.markdown = _fc("tg-markdown", c.markdown);
    return;
  }
  if (provider === "feishu") {
    const c = connectors.feishu;
    c.enabled = _fc("feishu-enabled", c.enabled);
    c.app_id = _fv("fs-appid");
    c.app_secret = _fv("fs-secret");
    c.encrypt_key = _fv("fs-encrypt-key");
    c.verification_token = _fv("fs-verify-token");
    c.agent = _fv("fs-agent") || "default";
    c.workspace = _fv("fs-workspace");
    c.allowlist = _fv("fs-allowlist");
    c.stream = _fc("fs-stream", c.stream);
    c.markdown = _fc("fs-markdown", c.markdown);
    return;
  }
  if (provider === "discord") {
    const c = connectors.discord;
    c.enabled = _fc("discord-enabled", c.enabled);
    c.bot_token = _fv("dc-token");
    c.agent = _fv("dc-agent") || "default";
    c.workspace = _fv("dc-workspace");
    c.allowlist = _fv("dc-allowlist");
    c.guild_allowlist = _fv("dc-guild-allowlist");
    c.require_mention = _fc("dc-require-mention", c.require_mention);
    c.stream = _fc("dc-stream", c.stream);
    c.markdown = _fc("dc-markdown", c.markdown);
    return;
  }
  if (provider === "slack") {
    const c = connectors.slack;
    c.enabled = _fc("slack-enabled", c.enabled);
    c.bot_token = _fv("sl-bot-token");
    c.app_token = _fv("sl-app-token");
    c.agent = _fv("sl-agent") || "default";
    c.workspace = _fv("sl-workspace");
    c.allowlist = _fv("sl-allowlist");
    c.stream = _fc("sl-stream", c.stream);
    c.markdown = _fc("sl-markdown", c.markdown);
    return;
  }
  if (provider === "dingtalk") {
    const c = connectors.dingtalk;
    c.enabled = _fc("dingtalk-enabled", c.enabled);
    c.client_id = _fv("dt-client-id");
    c.client_secret = _fv("dt-client-secret");
    c.agent = _fv("dt-agent") || "default";
    c.workspace = _fv("dt-workspace");
    c.allowlist = _fv("dt-allowlist");
    c.markdown = _fc("dt-markdown", c.markdown);
    return;
  }
  if (provider === "wecom") {
    const c = connectors.wecom;
    c.enabled = _fc("wecom-enabled", c.enabled);
    c.corp_id = _fv("wc-corp-id");
    c.corp_secret = _fv("wc-corp-secret");
    c.agent_id = parseInt(document.getElementById("wc-agent-id")?.value || "0", 10) || 0;
    c.token = _fv("wc-token");
    c.agent = _fv("wc-agent") || "default";
    c.workspace = _fv("wc-workspace");
    c.allowlist = _fv("wc-allowlist");
    c.markdown = _fc("wc-markdown", c.markdown);
  }
}

function _channelSaveConfig(provider) {
  if (provider === "telegram") {
    const c = connectors.telegram;
    const out = {
      enabled: c.enabled,
      agent: c.agent || "default",
      workspace: c.workspace || "",
      allowlist: _intList(c.allowlist),
      group_allowlist: _intList(c.group_allowlist),
      group_policy: c.group_policy || "allowlist",
      require_mention: c.require_mention,
      stream: c.stream,
      markdown: c.markdown !== false,
    };
    if (c.bot_token) out.bot_token = c.bot_token;
    return out;
  }
  if (provider === "feishu") {
    const c = connectors.feishu;
    const out = {
      enabled: c.enabled,
      agent: c.agent || "default",
      workspace: c.workspace || "",
      allowlist: _strList(c.allowlist),
      stream: c.stream,
      markdown: c.markdown !== false,
    };
    if (c.app_id) out.app_id = c.app_id;
    if (c.app_secret) out.app_secret = c.app_secret;
    if (c.encrypt_key) out.encrypt_key = c.encrypt_key;
    if (c.verification_token) out.verification_token = c.verification_token;
    return out;
  }
  if (provider === "discord") {
    const c = connectors.discord;
    const out = {
      enabled: c.enabled,
      agent: c.agent || "default",
      workspace: c.workspace || "",
      allowlist: _intList(c.allowlist),
      guild_allowlist: _intList(c.guild_allowlist),
      require_mention: c.require_mention,
      stream: c.stream,
      markdown: c.markdown !== false,
    };
    if (c.bot_token) out.bot_token = c.bot_token;
    return out;
  }
  if (provider === "slack") {
    const c = connectors.slack;
    const out = {
      enabled: c.enabled,
      agent: c.agent || "default",
      workspace: c.workspace || "",
      allowlist: _strList(c.allowlist),
      stream: c.stream,
      markdown: c.markdown !== false,
    };
    if (c.bot_token) out.bot_token = c.bot_token;
    if (c.app_token) out.app_token = c.app_token;
    return out;
  }
  if (provider === "dingtalk") {
    const c = connectors.dingtalk;
    const out = {
      enabled: c.enabled,
      agent: c.agent || "default",
      workspace: c.workspace || "",
      allowlist: _strList(c.allowlist),
      stream: c.stream,
      markdown: c.markdown !== false,
    };
    if (c.client_id) out.client_id = c.client_id;
    if (c.client_secret) out.client_secret = c.client_secret;
    return out;
  }
  const c = connectors.wecom;
  const out = {
    enabled: c.enabled,
    agent: c.agent || "default",
    workspace: c.workspace || "",
    agent_id: c.agent_id || 0,
    allowlist: _strList(c.allowlist),
    markdown: c.markdown !== false,
  };
  if (c.corp_id) out.corp_id = c.corp_id;
  if (c.corp_secret) out.corp_secret = c.corp_secret;
  if (c.token) out.token = c.token;
  return out;
}

function _saveChannelConfig(provider) {
  _syncChannelFormToState(provider);
  appConnectorsPanel.saveStatus = "Saving...";
  appConnectorsPanel.saveStatusType = "";
  _setModalStatus("app-connector-modal-save-status", "", appConnectorsPanel.saveStatus);
  send({
    type: "save_config",
    save_client_id: `conn_${Date.now()}_${provider}`,
    config: {
      connectors: {
        [provider]: _channelSaveConfig(provider),
      },
    },
  });
  appConnectorsPanel.saveStatus = "Save requested. Runtime status will refresh after reload.";
  appConnectorsPanel.saveStatusType = "ok";
  _setModalStatus("app-connector-modal-save-status", "ok", appConnectorsPanel.saveStatus);
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
  if (id === "google_workspace") {
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
  const item = _connectionsDirectoryItems().find((entry) => entry.id === id) || _normalizeConnectionItem({ id });
  appConnectorsPanel.selected = item.id;
  appConnectorsPanel.saveStatus = "";
  appConnectorsPanel.testStatus = "";
  const appId = item.manage_id || item.id;
  const isAppPanel = item.manage_target === "panel" && APP_PANEL_IDS.has(appId);
  const isChannel = item.kind === "channel";
  const modalHtml = isAppPanel
    ? (appId === "github"
      ? _renderGitHubConfigModal()
      : appId === "google_workspace"
        ? _renderGoogleWorkspaceConfigModal(item)
        : appId === "notion"
          ? _renderNotionConfigModal(item)
          : appId === "jira"
            ? _renderJiraConfigModal(item)
            : appId === "reddit"
              ? _renderRedditConfigModal(item)
              : _renderXConfigModal(item))
    : isChannel
      ? _renderChannelConfigModal(item)
      : _renderConnectionDetailsModal(item);
  openDialog({
    title: `${isAppPanel || isChannel ? "Configure" : "View"} ${item.name}`,
    html: modalHtml,
    closeOnBackdrop: true,
    actions: [
      { label: "Close", secondary: true, onClick: closeModal },
    ],
  });
  if (isChannel) {
    document.getElementById(`btn-save-channel-${item.provider}`)?.addEventListener("click", () => _saveChannelConfig(item.provider));
    document.getElementById(`btn-refresh-channel-${item.provider}`)?.addEventListener("click", () => send({ type: "get_config_status" }));
    return;
  }
  if (!isAppPanel) {
    document.getElementById("btn-open-connection-settings")?.addEventListener("click", () => _openConnectionSettings(item));
    document.getElementById("btn-refresh-connections-modal")?.addEventListener("click", () => send({ type: "get_config_status" }));
    return;
  }
  if (appId === "github") _bindGitHubConfig();
  else _bindConnectorActions(appId);
}

export function renderAppConnectorsPanel() {
  const root = document.getElementById("app-connectors-content");
  if (!root) return;
  root.innerHTML = `
    <div class="app-connectors-header">
      <div>
        <div class="app-connectors-eyebrow">Unified external integrations</div>
        <h1>Connections</h1>
        <p>Manage apps, channels, and sync sources from one directory. Product features like Calendar still run on local normalized data, while Connections tracks the external integrations behind them.</p>
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
  });
}

export function handleTestAppConnectorResult(data) {
  appConnectorsPanel.testStatus = data.ok ? `Connected. ${data.message || ""}` : `Failed. ${data.message || ""}`;
  appConnectorsPanel.testStatusType = data.ok ? "ok" : "err";
  _setModalStatus("app-connector-modal-test-status", appConnectorsPanel.testStatusType, appConnectorsPanel.testStatus);
  renderAppConnectorsPanel();
}
