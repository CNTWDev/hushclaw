/**
 * channels.js — Standalone Channels panel (inline tab, auto-save).
 *
 * Reuses CHANNELS definitions from settings.js so field rendering stays
 * in one place. Auto-saves on every toggle/input change (no Save button).
 */

import {
  CHANNELS, syncFormToState, saveSettings,
} from "./settings.js";
import { connectors, wizard, escHtml } from "./state.js";

// ── Helpers ────────────────────────────────────────────────────────────────

function _isConfigured(platform, c) {
  switch (platform) {
    case "telegram":  return c.bot_token_set || !!c.bot_token;
    case "feishu":    return c.app_secret_set || !!(c.app_id && c.app_secret);
    case "discord":   return c.bot_token_set || !!c.bot_token;
    case "slack":     return c.bot_token_set || c.app_token_set || !!(c.bot_token && c.app_token);
    case "dingtalk":  return c.client_secret_set || !!(c.client_id && c.client_secret);
    case "wecom":     return c.corp_secret_set || !!(c.corp_id && c.corp_secret);
    default:          return false;
  }
}

let _saveDebounceTimer = null;

function _scheduleSave() {
  if (_saveDebounceTimer) clearTimeout(_saveDebounceTimer);
  _saveDebounceTimer = setTimeout(() => {
    _saveDebounceTimer = null;
    syncFormToState();
    saveSettings();
  }, 800);
}

function _saveNow() {
  if (_saveDebounceTimer) { clearTimeout(_saveDebounceTimer); _saveDebounceTimer = null; }
  syncFormToState();
  saveSettings();
}

// ── Main render ────────────────────────────────────────────────────────────

export function renderChannelsPanel() {
  const container = document.getElementById("channels-content");
  if (!container) return;

  const status = wizard.connectorStatus || {};

  container.innerHTML =
    `<div class="channels-panel-wrap">` +
    `<div class="channels-panel-title">Channels</div>` +
    `<div class="conn-panel">` +
    CHANNELS.map((ch) => {
      const c          = connectors[ch.id];
      const on         = c.enabled;
      const configured = _isConfigured(ch.id, c);
      const isOnline   = status[ch.id] === true;
      const dotClass   = `conn-status-dot ${isOnline ? "online" : "offline"}`;
      const dotTitle   = isOnline ? "Connected" : (on ? "Starting / offline" : "Disabled");
      const badge      = (!on && configured)
        ? `<span class="conn-configured-badge" title="Previously configured — toggle to re-enable">configured</span>`
        : "";

      return `
        <div class="conn-section" id="chan-${ch.id}">
          <div class="conn-section-header">
            <span class="conn-platform-icon">${ch.icon}</span>
            <div class="conn-platform-info">
              <span class="conn-platform-name">${ch.name}</span>
              <span class="conn-platform-desc">${ch.desc}</span>
            </div>
            ${badge}
            <span class="${dotClass}" title="${dotTitle}"></span>
            <label class="toggle-switch" title="${on ? "Enabled" : "Disabled"}">
              <input type="checkbox" id="${ch.id}-enabled" ${on ? "checked" : ""}
                     data-chan="${ch.id}">
              <span class="toggle-slider"></span>
            </label>
          </div>
          <div class="conn-fields" id="${ch.id}-fields"${on ? "" : ' style="display:none"'}>
            ${ch.fields(c)}
            <div class="wfield-hint" style="margin-top:4px">
              Setup guide: <a href="${ch.setupUrl}" target="_blank" rel="noopener">${ch.setupLabel} ↗</a>
            </div>
          </div>
        </div>`;
    }).join("") +
    `</div></div>`;

  // ── Bind events ────────────────────────────────────────────────────────
  CHANNELS.forEach(({ id }) => {
    const togEl = document.getElementById(`${id}-enabled`);
    const fieldsEl = document.getElementById(`${id}-fields`);
    if (!togEl || !fieldsEl) return;

    // Toggle: expand/collapse fields + immediate save
    togEl.addEventListener("change", (e) => {
      fieldsEl.style.display = e.target.checked ? "" : "none";
      _saveNow();
    });

    // Text/password/select/number inputs: debounced save
    fieldsEl.querySelectorAll("input, select, textarea").forEach((el) => {
      el.addEventListener("change", _scheduleSave);
      if (el.type !== "checkbox") {
        el.addEventListener("input", _scheduleSave);
      } else {
        // Inline checkboxes (stream, markdown, require_mention): immediate
        el.addEventListener("change", _saveNow);
      }
    });
  });
}

// Re-export so websocket.js can call it when config_status arrives
export function updateChannelStatusDots() {
  const status = wizard.connectorStatus || {};
  CHANNELS.forEach(({ id }) => {
    const dotEl = document.querySelector(`#chan-${id} .conn-status-dot`);
    if (!dotEl) return;
    const isOnline = status[id] === true;
    const c = connectors[id];
    dotEl.className = `conn-status-dot ${isOnline ? "online" : "offline"}`;
    dotEl.title = isOnline ? "Connected" : (c?.enabled ? "Starting / offline" : "Disabled");
  });
}
