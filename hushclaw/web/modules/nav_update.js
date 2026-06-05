/**
 * nav_update.js — Left rail update / force-upgrade entry.
 */

import { wizard, updateState, showToast } from "./state.js";
import { requestForceUpgrade, requestRunUpdate } from "./updates.js";

function _devModeOn() {
  try { return localStorage.getItem("hushclaw.dev.mode") === "1"; } catch { return false; }
}

function _els() {
  return {
    btn: document.getElementById("nav-update-action"),
    label: document.getElementById("nav-update-label"),
    badge: document.getElementById("nav-update-badge"),
  };
}

function _navUpdateState() {
  const hasUpdate = Boolean(wizard.updateAvailable);
  const devOn = _devModeOn();
  if (!devOn && !hasUpdate) return { visible: false };
  if (updateState.upgrading) {
    return { visible: true, label: "Upgrading", badge: hasUpdate, disabled: true, force: false };
  }
  if (updateState.checking) {
    return { visible: true, label: "Checking", badge: hasUpdate, disabled: true, force: false };
  }
  if (hasUpdate) {
    return { visible: true, label: "Upgrade", badge: true, disabled: false, force: false };
  }
  return { visible: true, label: "Force", badge: false, disabled: false, force: true };
}

export function refreshNavUpdateAction() {
  const { btn, label, badge } = _els();
  if (!btn) return;
  const view = _navUpdateState();
  btn.classList.toggle("hidden", !view.visible);
  if (!view.visible) return;

  btn.disabled = Boolean(view.disabled);
  btn.dataset.force = view.force ? "1" : "0";
  btn.dataset.label = view.force ? "Force Upgrade" : view.label;
  btn.title = view.force ? "Dev mode: force upgrade HushClaw" : "Upgrade HushClaw";
  btn.classList.toggle("has-update", Boolean(view.badge));
  btn.classList.toggle("is-force", Boolean(view.force));
  btn.classList.toggle("is-busy", Boolean(view.disabled));
  if (label) label.textContent = view.label;
  if (badge) badge.classList.toggle("hidden", !view.badge);
}

export function initNavUpdateAction() {
  const { btn } = _els();
  if (!btn || btn.dataset.bound === "1") return;
  btn.dataset.bound = "1";
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    if (btn.dataset.force === "1") {
      requestForceUpgrade();
      return;
    }
    if (!wizard.updateAvailable) {
      showToast("No update is currently available.", "info");
      refreshNavUpdateAction();
      return;
    }
    requestRunUpdate();
  });
  refreshNavUpdateAction();
}
