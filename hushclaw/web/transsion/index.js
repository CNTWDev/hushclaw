/**
 * transsion/index.js — Plugin entry point.
 *
 * This is the ONLY file that touches the HushClaw plugin-host API.
 * Everything else in transsion/ is self-contained.
 *
 * Boot sequence:
 *   1. Load forum.css dynamically (so index.html stays clean).
 *   2. initAuth() — start listening for "hc:transsion-authed" events.
 *   3. If already authed (token in localStorage) → register the Forum tab now.
 *   4. On "hc:forum-ready"   → register the Forum tab (first login this session).
 *   5. On "hc:forum-unauthed"→ un-register / show login prompt.
 *   6. Register auth-widget into the Settings Channels tab.
 */

import { registerSidePlugin } from "../modules/plugin-host.js";
import { initAuth, isAuthed }  from "./auth.js";
import { onForumActivate, onForumUnauthed } from "./forum.js";
import "./auth-widget.js";   // registers itself via registerSettingsWidget()

// ── Load CSS ─────────────────────────────────────────────────────────────────

(function _loadCss() {
  const id = "hc-forum-css";
  if (document.getElementById(id)) return;
  const link  = document.createElement("link");
  link.id     = id;
  link.rel    = "stylesheet";
  link.href   = "/transsion/forum.css";
  document.head.appendChild(link);
})();

// ── Init auth listener ────────────────────────────────────────────────────────

initAuth();

// ── Register the Forum tab ────────────────────────────────────────────────────

let _pluginRegistered = false;

function _ensurePluginRegistered() {
  if (_pluginRegistered) return;
  _pluginRegistered = true;
  registerSidePlugin({
    tabId:      "forum",
    label:      "Forum",
    onActivate: onForumActivate,
  });
}

// Show Forum tab immediately if token already exists (e.g. after page refresh).
if (isAuthed()) {
  _ensurePluginRegistered();
}

// Show Forum tab on successful fresh login this session.
document.addEventListener("hc:forum-ready", _ensurePluginRegistered);

// Handle token expiry / logout.
document.addEventListener("hc:forum-unauthed", () => {
  onForumUnauthed();
  // We keep the tab registered but it will now show the login prompt.
});
