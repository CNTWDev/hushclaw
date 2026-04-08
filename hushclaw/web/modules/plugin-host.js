/**
 * plugin-host.js — Lightweight side-panel plugin registry for HushClaw.
 *
 * Plugins call registerSidePlugin() to dynamically inject a Tab button
 * and panel <div> into the page. The host knows nothing about plugin
 * internals; it only stores the onActivate callback and calls it when
 * the user switches to that tab.
 *
 * Core modules (panels.js) import notifyTabActivated() to fire callbacks.
 */

import { debugUiLifecycle } from "./state.js";

// Map of tabId → { onActivate }
const _plugins = new Map();

/**
 * Register a side-panel plugin.
 *
 * @param {object} opts
 * @param {string}   opts.tabId      — unique tab identifier (used in data-tab + panel-<id>)
 * @param {string}   opts.label      — Tab button label
 * @param {Function} [opts.onActivate] — called each time the user switches to this tab
 */
export function registerSidePlugin({ tabId, label, onActivate }) {
  if (_plugins.has(tabId)) return; // idempotent
  _plugins.set(tabId, { onActivate });
  _injectTabButton(tabId, label);
  _injectPanelDiv(tabId);
  debugUiLifecycle("plugin_registered", { tabId });
}

/**
 * Called by panels.js switchTab() for every tab switch.
 * Fires the plugin's onActivate callback if the tab belongs to a plugin.
 */
export function notifyTabActivated(tabId) {
  const plugin = _plugins.get(tabId);
  plugin?.onActivate?.();
}

/** Show the tab button for a registered plugin. */
export function showPlugin(tabId) {
  const btn = document.querySelector(`nav.tabs [data-tab="${tabId}"]`);
  if (btn) btn.style.display = "";
}

/** Hide the tab button for a registered plugin (panel remains in DOM but inaccessible). */
export function hidePlugin(tabId) {
  const btn = document.querySelector(`nav.tabs [data-tab="${tabId}"]`);
  if (btn) btn.style.display = "none";
  // If the user is currently on this tab, switch back to chat.
  if (btn?.classList.contains("active")) {
    import("./panels.js").then(({ switchTab }) => switchTab("chat"));
  }
}

// ── Internal helpers ────────────────────────────────────────────────────────

function _injectTabButton(tabId, label) {
  const nav = document.querySelector("nav.tabs");
  if (!nav) return;
  // Avoid duplicates (e.g. if module is evaluated twice during hot-reload)
  if (nav.querySelector(`[data-tab="${tabId}"]`)) return;

  const btn = document.createElement("button");
  btn.className   = "tab";
  btn.dataset.tab = tabId;
  btn.textContent = label;
  // Tab click is already handled by events.js via querySelectorAll(".tab"),
  // but new buttons added after that listener runs won't be covered.
  // Import switchTab lazily to avoid circular deps.
  btn.addEventListener("click", () => {
    import("./panels.js").then(({ switchTab }) => switchTab(tabId));
  });
  nav.appendChild(btn);
}

function _injectPanelDiv(tabId) {
  const main = document.querySelector("main");
  if (!main) return;
  if (document.getElementById(`panel-${tabId}`)) return;

  const div = document.createElement("div");
  div.id        = `panel-${tabId}`;
  div.className = "panel";
  main.appendChild(div);
}
