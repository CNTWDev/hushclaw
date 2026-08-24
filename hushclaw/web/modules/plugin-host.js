/**
 * Composable WebUI host.
 *
 * Extensions can contribute either a full product panel or a small, ordered
 * surface mounted into a named UI slot. Every registration returns a disposer;
 * unloading an extension therefore unwinds its DOM and cleanup effects.
 */

import { debugUiLifecycle } from "./state.js";

const _panels = new Map();
const _slots = new Map();
const _slotCleanups = new Map();

function _assertId(value, label) {
  const id = String(value || "").trim();
  if (!id || !/^[a-z0-9][a-z0-9._:-]*$/i.test(id)) {
    throw new TypeError(`${label} must be a non-empty, stable identifier`);
  }
  return id;
}

/** Register a top-level panel and return an effect disposer. */
export function registerSidePlugin({ tabId, label, icon, onActivate }) {
  const id = _assertId(tabId, "tabId");
  if (_panels.has(id)) return _panels.get(id).dispose;

  _injectTabButton(id, String(label || id), icon);
  _injectPanelDiv(id);

  const dispose = () => unregisterSidePlugin(id);
  _panels.set(id, { onActivate, dispose });
  debugUiLifecycle("plugin_registered", { tabId: id });
  return dispose;
}

/** Remove a top-level panel and all host-owned DOM for it. */
export function unregisterSidePlugin(tabId) {
  const id = String(tabId || "");
  if (!_panels.delete(id)) return false;
  const button = _findTab(id);
  const wasActive = button?.classList.contains("active");
  button?.remove();
  document.getElementById(`panel-${id}`)?.remove();
  if (wasActive) import("./panels.js").then(({ switchTab }) => switchTab("chat"));
  debugUiLifecycle("plugin_unregistered", { tabId: id });
  return true;
}

/** Called by panels.js when a top-level panel becomes active. */
export function notifyTabActivated(tabId) {
  _panels.get(tabId)?.onActivate?.();
}

/**
 * Mount an ordered contribution into every matching `data-ui-slot` surface.
 *
 * `mount(host)` may append directly, return a Node, return a cleanup callback,
 * or return `{ node, cleanup }`. The disposer is safe to call repeatedly.
 */
export function registerUiSlot({ slotId, pluginId, order = 0, mount }) {
  const slot = _assertId(slotId, "slotId");
  const plugin = _assertId(pluginId, "pluginId");
  if (typeof mount !== "function") throw new TypeError("mount must be a function");

  let entries = _slots.get(slot);
  if (!entries) {
    entries = new Map();
    _slots.set(slot, entries);
  }
  if (entries.has(plugin)) unregisterUiSlot(slot, plugin);
  entries.set(plugin, { pluginId: plugin, order: Number(order) || 0, mount });
  _renderSlot(slot);
  debugUiLifecycle("ui_slot_registered", { slotId: slot, pluginId: plugin });
  return () => unregisterUiSlot(slot, plugin);
}

/** Unmount one contribution and replay the remaining ordered effects. */
export function unregisterUiSlot(slotId, pluginId) {
  const slot = String(slotId || "");
  const plugin = String(pluginId || "");
  const entries = _slots.get(slot);
  if (!entries?.delete(plugin)) return false;
  if (!entries.size) _slots.delete(slot);
  _renderSlot(slot);
  debugUiLifecycle("ui_slot_unregistered", { slotId: slot, pluginId: plugin });
  return true;
}

/** Replay all registered slots after a shell subtree is replaced. */
export function mountUiSlots() {
  for (const slotId of _slots.keys()) _renderSlot(slotId);
}

export function showPlugin(tabId) {
  const btn = _findTab(tabId);
  if (btn) btn.hidden = false;
}

export function hidePlugin(tabId) {
  const btn = _findTab(tabId);
  if (btn) btn.hidden = true;
  if (btn?.classList.contains("active")) {
    import("./panels.js").then(({ switchTab }) => switchTab("chat"));
  }
}

function _findTab(tabId) {
  return Array.from(document.querySelectorAll("nav.tabs [data-tab]"))
    .find((node) => node.dataset.tab === tabId) || null;
}

function _slotHosts(slotId) {
  return Array.from(document.querySelectorAll("[data-ui-slot]"))
    .filter((node) => node.dataset.uiSlot === slotId);
}

function _cleanupSlot(slotId) {
  for (const cleanup of _slotCleanups.get(slotId) || []) {
    try { cleanup(); } catch (error) { console.warn("UI slot cleanup failed", error); }
  }
  _slotCleanups.delete(slotId);
  for (const host of _slotHosts(slotId)) host.replaceChildren();
}

function _renderSlot(slotId) {
  _cleanupSlot(slotId);
  const entries = Array.from(_slots.get(slotId)?.values() || [])
    .sort((a, b) => (a.order - b.order) || a.pluginId.localeCompare(b.pluginId));
  if (!entries.length) return;

  const cleanups = [];
  for (const slotHost of _slotHosts(slotId)) {
    for (const entry of entries) {
      const host = document.createElement("div");
      host.className = "ui-slot-contribution";
      host.dataset.uiPlugin = entry.pluginId;
      host.dataset.uiOrder = String(entry.order);
      slotHost.appendChild(host);
      const result = entry.mount(host);
      if (result instanceof Node) host.appendChild(result);
      else if (typeof result === "function") cleanups.push(result);
      else if (result && typeof result === "object") {
        if (result.node instanceof Node) host.appendChild(result.node);
        if (typeof result.cleanup === "function") cleanups.push(result.cleanup);
      }
    }
  }
  if (cleanups.length) _slotCleanups.set(slotId, cleanups);
}

function _injectTabButton(tabId, label, icon) {
  const nav = document.querySelector("nav.tabs");
  if (!nav || _findTab(tabId)) return;

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "tab";
  btn.dataset.tab = tabId;
  if (icon) {
    const iconWrapper = document.createElement("span");
    iconWrapper.innerHTML = icon;
    const firstChild = iconWrapper.firstElementChild;
    if (firstChild) btn.appendChild(firstChild);
  }
  const text = document.createElement("span");
  text.textContent = label;
  btn.appendChild(text);
  btn.addEventListener("click", () => {
    import("./panels.js").then(({ switchTab }) => switchTab(tabId));
  });
  nav.insertBefore(btn, nav.querySelector(".tab-settings"));
}

function _injectPanelDiv(tabId) {
  const main = document.querySelector("main");
  if (!main || document.getElementById(`panel-${tabId}`)) return;
  const panel = document.createElement("div");
  panel.id = `panel-${tabId}`;
  panel.className = "panel";
  panel.dataset.pluginPanel = tabId;
  main.appendChild(panel);
}
