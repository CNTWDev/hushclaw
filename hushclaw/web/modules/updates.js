/**
 * updates.js — update checks, upgrade prompts, and progress handling.
 */

import { state, wizard, updateState, send, showToast, els } from "./state.js";
import { insertSystemMsg } from "./chat.js";
import { openConfirm, openLiveModal, closeModal } from "./modal.js";

// Ensure the app performs one version-check attempt on startup, then falls back
// to the normal interval-based auto-check policy for the rest of the session.
let _startupVersionCheckIssued = false;

// ── Upgrade progress modal ─────────────────────────────────────────────────

/** Live modal handle; non-null while upgrade is in progress. */
let _liveModal = null;
/** Accumulated log lines shown inside the modal during the upgrade phase. */
let _upgradeLog = [];

function _esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function _spinnerHtml(headline, sub = "") {
  return (
    `<div class="upg-body">` +
    `<div class="upg-spinner"></div>` +
    `<p class="upg-headline">${_esc(headline)}</p>` +
    (sub ? `<p class="upg-sub">${_esc(sub)}</p>` : "") +
    `</div>`
  );
}

function _spinnerWithLogHtml(headline) {
  const lines = _upgradeLog
    .slice(-40)
    .map((l) => `<div class="upg-log-line">${_esc(l)}</div>`)
    .join("");
  return (
    `<div class="upg-body">` +
    `<div class="upg-spinner"></div>` +
    `<p class="upg-headline">${_esc(headline)}</p>` +
    (lines ? `<div class="upg-log" id="upg-log">${lines}</div>` : "") +
    `</div>`
  );
}

function _doneHtml(ok, headline, sub = "") {
  const cls = ok ? "upg-done-icon--ok" : "upg-done-icon--warn";
  const icon = ok ? "✓" : "⚠";
  return (
    `<div class="upg-body">` +
    `<div class="upg-done-icon ${cls}">${icon}</div>` +
    `<p class="upg-headline">${_esc(headline)}</p>` +
    (sub ? `<p class="upg-sub">${_esc(sub)}</p>` : "") +
    `</div>`
  );
}

function _scrollUpgLog() {
  const el = document.getElementById("upg-log");
  if (el) el.scrollTop = el.scrollHeight;
}

function _settleModal(ok, headline, sub = "") {
  if (!_liveModal) return;
  _liveModal.settle({
    html: _doneHtml(ok, headline, sub),
    actions: [{ label: "OK", onClick: () => closeModal() }],
  });
  _liveModal = null;
  _upgradeLog = [];
}

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
  if (!_startupVersionCheckIssued) {
    _startupVersionCheckIssued = true;
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
    requestCheckUpdate(false);
    return;
  }
  const intervalHours = Math.max(1, Number(upd.check_interval_hours || 24));
  const last = Number(upd.last_checked_at || 0);
  const now = Math.floor(Date.now() / 1000);
  if (now - last < intervalHours * 3600) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  requestCheckUpdate(false);
}

export function requestCheckUpdate(force = true) {
  updateState.checking = true;
  updateState.manualCheck = force;  // only show error toast when user explicitly triggered check
  refreshUpdateUi();
  send({
    type: "check_update",
    force: Boolean(force),
    channel: wizard.updateChannel || "stable",
  });
}

export function requestRunUpdate(forceWhenBusy = false) {
  // Snapshot the current version so the post-reconnect check can verify the
  // upgrade actually changed the binary, not just restarted the server.
  updateState.versionBeforeUpgrade = wizard.updateCurrentVersion || "";
  updateState.upgrading = true;
  // The upgrade script (install.sh --update) terminates the running server
  // process as part of its flow, which drops the WebSocket.  Mark this flag
  // so the reconnect handler can distinguish an expected upgrade disconnect
  // from an ordinary network error and treat it as a successful update.
  updateState.expectingDisconnect = true;
  // Persist across potential page refresh (sessionStorage cleared on tab close).
  try { sessionStorage.setItem("hc_upgrade_pending", "1"); } catch {}
  refreshUpdateUi();

  // Open the upgrade progress modal — non-dismissible until done.
  _upgradeLog = [];
  _liveModal = openLiveModal({ title: "Upgrading HushClaw" });
  _liveModal.update(_spinnerHtml("Sending upgrade request…"));

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
  const newVersion = data.current_version || wizard.updateCurrentVersion || "";
  wizard.updateCurrentVersion = newVersion;
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

  // Deferred upgrade result: if we reconnected after an expected upgrade
  // disconnect, now we know the real new version — verify it actually changed.
  if (updateState.verifyingUpgrade) {
    updateState.verifyingUpgrade = false;
    const prev = updateState.versionBeforeUpgrade;
    updateState.versionBeforeUpgrade = "";
    if (newVersion && prev && newVersion !== prev) {
      showToast(`Upgraded ${prev} → ${newVersion}`, "ok");
      insertSystemMsg(`✓ Upgrade confirmed: ${prev} → ${newVersion}`);
      _settleModal(true, "Upgrade complete", `${prev} → ${newVersion}`);
    } else if (newVersion && prev && newVersion === prev) {
      showToast("Reconnected, but version unchanged — upgrade may have failed.", "warn");
      insertSystemMsg(`⚠ Server restarted but version is still ${newVersion}. Check server logs.`);
      _settleModal(false, "Version unchanged", `Still on ${newVersion} — check server logs.`);
    } else {
      showToast("Reconnected after upgrade.", "ok");
      insertSystemMsg("✓ Server restarted after upgrade. Reconnected.");
      _settleModal(true, "Upgrade complete", "Server restarted successfully.");
    }
    refreshUpdateUi();
    return;
  }

  refreshUpdateUi();
  if (!data.ok && updateState.manualCheck) {
    showToast(`Update check failed: ${data.error || "unknown error"}`, "warn");
  }
  updateState.manualCheck = false;
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
    if (_liveModal) {
      _upgradeLog.push(data.message);
      _liveModal.update(_spinnerWithLogHtml("Upgrading…"));
      _scrollUpgLog();
    }
  }
}

export function handleUpdateResult(data) {
  updateState.checking = false;
  if (data.ok) {
    // Server will exit in ~1 s — keep upgrading + expectingDisconnect flags
    // set so the reconnect handler knows to verify the new version.
    // Modal stays open; it will transition to "Server restarting…" once the
    // server_shutdown event (or ws onclose) arrives.
    insertSystemMsg("Update delegate launched — server restarting…");
    if (_liveModal) {
      _liveModal.update(_spinnerHtml("Server restarting…", "Waiting for new server to come online."));
    }
  } else {
    updateState.upgrading = false;
    updateState.expectingDisconnect = false;
    try { sessionStorage.removeItem("hc_upgrade_pending"); } catch {}
    refreshUpdateUi();
    const err = data.error || "unknown error";
    showToast(`Update failed: ${err}`, "error");
    insertSystemMsg(`Update failed: ${err}`);
    // Settle modal with error state.
    _settleModal(false, "Upgrade failed", err);
    if (err.includes("active sessions")) {
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

export function handleServerShutdown(data) {
  const reason = data.reason || "unknown";
  if (reason === "upgrade") {
    if (!updateState.expectingDisconnect) {
      // Server-initiated shutdown without a prior run_update from this tab
      // (e.g. another tab triggered the upgrade).
      updateState.expectingDisconnect = true;
      updateState.upgrading = true;
      updateState.versionBeforeUpgrade = wizard.updateCurrentVersion || "";
      try { sessionStorage.setItem("hc_upgrade_pending", "1"); } catch {}
      refreshUpdateUi();
      // Open modal for tabs that weren't the one that triggered the upgrade.
      if (!_liveModal) {
        _upgradeLog = [];
        _liveModal = openLiveModal({ title: "Upgrading HushClaw" });
      }
    }
    insertSystemMsg("Server is restarting for upgrade — reconnecting shortly…");
    if (_liveModal) {
      _liveModal.update(_spinnerHtml("Server restarting…", "Waiting for new server to come online."));
    }
  } else {
    insertSystemMsg(`Server is shutting down (${reason}).`);
  }
}

/**
 * Called by websocket.js onopen when a reconnect follows an expected upgrade
 * disconnect.  Updates the modal to "verifying" state before the version
 * check resolves.
 */
export function notifyUpgradeReconnected() {
  if (!_liveModal) return;
  _liveModal.update(_spinnerHtml("Reconnected", "Verifying upgrade…"));
}

/**
 * Dev-mode only: bypass version comparison and trigger the upgrade flow
 * immediately.  Useful for testing the full upgrade log chain locally.
 */
export function requestForceUpgrade() {
  wizard.updateAvailable = true;
  requestRunUpdate();
}
