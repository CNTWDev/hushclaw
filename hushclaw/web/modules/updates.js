/**
 * updates.js — update checks, upgrade prompts, and progress handling.
 */

import { state, wizard, updateState, send, showToast, els } from "./state.js";
import { insertSystemMsg } from "./chat.js";
import { openConfirm } from "./modal.js";

function _fmtTs(unixSec) {
  if (!unixSec) return "never";
  const d = new Date(unixSec * 1000);
  if (Number.isNaN(d.getTime())) return "unknown";
  return d.toLocaleString();
}

function _setCheckButtonState() {
  const btn = document.getElementById("upd-check-btn");
  if (!btn) return;
  btn.disabled = updateState.checking || updateState.upgrading;
  btn.textContent = updateState.checking ? "Checking..." : "Check now";
}

function _setUpgradeButtonState() {
  const btn = document.getElementById("upd-upgrade-btn");
  if (!btn) return;
  const canUpgrade = Boolean(wizard.updateAvailable);
  btn.disabled = updateState.checking || updateState.upgrading || !canUpgrade;
  btn.textContent = updateState.upgrading ? "Upgrading..." : "Upgrade now";
}

export function refreshUpdateUi() {
  const statusEl = document.getElementById("upd-status");
  if (statusEl) {
    const latest = wizard.updateLatestVersion || "unknown";
    const current = wizard.updateCurrentVersion || "unknown";
    statusEl.textContent = `Current ${current} · Latest ${latest} · Last check ${_fmtTs(wizard.updateLastCheckedAt)}`;
  }
  _setCheckButtonState();
  _setUpgradeButtonState();
}

export function maybeAutoCheckUpdates(cfg) {
  const upd = cfg?.update || {};
  const enabled = upd.auto_check_enabled !== false;
  if (!enabled) return;
  const intervalHours = Math.max(1, Number(upd.check_interval_hours || 24));
  const last = Number(upd.last_checked_at || 0);
  const now = Math.floor(Date.now() / 1000);
  if (now - last < intervalHours * 3600) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  requestCheckUpdate(false);
}

export function requestCheckUpdate(force = true) {
  updateState.checking = true;
  refreshUpdateUi();
  send({
    type: "check_update",
    force: Boolean(force),
    channel: wizard.updateChannel || "stable",
  });
}

export function requestRunUpdate(forceWhenBusy = false) {
  // #region agent log
  fetch('http://127.0.0.1:7866/ingest/27d763d0-b753-40be-a694-9f8daadda668',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'dc60c9'},body:JSON.stringify({sessionId:'dc60c9',location:'updates.js:requestRunUpdate',message:'run_update_called',data:{forceWhenBusy,upgrading:updateState.upgrading,expectingDisconnect:updateState.expectingDisconnect},timestamp:Date.now()})}).catch(()=>{});
  // #endregion
  updateState.upgrading = true;
  // The upgrade script (install.sh --update) terminates the running server
  // process as part of its flow, which drops the WebSocket.  Mark this flag
  // so the reconnect handler can distinguish an expected upgrade disconnect
  // from an ordinary network error and treat it as a successful update.
  updateState.expectingDisconnect = true;
  // Persist across potential page refresh (sessionStorage cleared on tab close).
  try { sessionStorage.setItem("hc_upgrade_pending", "1"); } catch {}
  refreshUpdateUi();
  send({
    type: "run_update",
    target_version: wizard.updateLatestVersion || "",
    force_when_busy: Boolean(forceWhenBusy),
  });
}

async function _openUpgradeConfirm(data) {
  const lines = [
    `Current: ${data.current_version || wizard.updateCurrentVersion || "unknown"}`,
    `Latest: ${data.latest_version || wizard.updateLatestVersion || "unknown"}`,
    data.published_at ? `Published: ${data.published_at}` : "",
    data.release_url ? `Release: ${data.release_url}` : "",
    "",
    "Upgrading may briefly restart the service.",
  ].filter(Boolean);
  const ok = await openConfirm({
    title: "Update available",
    message: lines.join("\n"),
    confirmText: "Upgrade now",
    cancelText: "Later",
  });
  if (ok) requestRunUpdate();
  else showToast("Update postponed", "info");
}

export function handleUpdateStatus(data) {
  updateState.checking = false;
  updateState.lastStatus = data;
  wizard.updateCurrentVersion = data.current_version || wizard.updateCurrentVersion || "";
  wizard.updateLatestVersion = data.latest_version || "";
  wizard.updateAvailable = Boolean(data.update_available);
  wizard.updateReleaseUrl = data.release_url || "";
  wizard.updateLastCheckedAt = Math.floor(Date.now() / 1000);
  send({
    type: "save_update_policy",
    config: {
      auto_check_enabled: wizard.updateAutoCheckEnabled,
      check_interval_hours: wizard.updateCheckIntervalHours,
      channel: wizard.updateChannel || "stable",
      last_checked_at: wizard.updateLastCheckedAt || 0,
    },
  });
  refreshUpdateUi();
  if (!data.ok) {
    showToast(`Update check failed: ${data.error || "unknown error"}`, "error");
  }
}

export function handleUpdateAvailable(data) {
  wizard.updateCurrentVersion = data.current_version || wizard.updateCurrentVersion || "";
  wizard.updateLatestVersion = data.latest_version || wizard.updateLatestVersion || "";
  wizard.updateAvailable = true;
  wizard.updateReleaseUrl = data.release_url || wizard.updateReleaseUrl || "";
  refreshUpdateUi();

  _openUpgradeConfirm(data);
}

export function handleUpdateProgress(data) {
  if (data.status === "running") {
    updateState.upgrading = true;
  }
  refreshUpdateUi();
  if (data.message) {
    insertSystemMsg(`[update/${data.stage}] ${data.message}`);
  }
}

export function handleUpdateResult(data) {
  updateState.checking = false;
  updateState.upgrading = false;
  updateState.expectingDisconnect = false;
  try { sessionStorage.removeItem("hc_upgrade_pending"); } catch {}
  refreshUpdateUi();
  if (data.ok) {
    showToast("Update completed successfully.", "ok");
    insertSystemMsg("Update completed. Reconnect may occur if service restarts.");
  } else {
    showToast(`Update failed: ${data.error || "unknown error"}`, "error");
    insertSystemMsg(`Update failed: ${data.error || "unknown error"}`);
    if ((data.error || "").includes("active sessions")) {
      openConfirm({
        title: "Active sessions detected",
        message: "There are active sessions. Force upgrade anyway?",
        confirmText: "Force upgrade",
        cancelText: "Cancel",
        dangerConfirm: true,
      }).then((ok) => {
        if (!ok) return;
        requestRunUpdate(true);
      });
    }
  }
}
