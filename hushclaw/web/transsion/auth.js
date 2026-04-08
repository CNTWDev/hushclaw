/**
 * transsion/auth.js — Transsion community SSO token management.
 *
 * Completely self-contained: reads/writes localStorage only.
 * Does NOT import from or write to the core state.js wizard object.
 *
 * Token lifecycle:
 *   1. settings.js dispatches "hc:transsion-authed" on successful login.
 *   2. initAuth() listens for that event and persists token + user info.
 *   3. getToken() / getUser() are used by api.js and forum.js.
 *   4. clearToken() is called by api.js on 401 responses.
 */

const TOKEN_KEY = "hc_txn_token";
const USER_KEY  = "hc_txn_user";

// ── Token access ────────────────────────────────────────────────────────────

export function getToken()  { return localStorage.getItem(TOKEN_KEY) || ""; }
export function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
export function clearToken()  {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

// ── User info ───────────────────────────────────────────────────────────────

export function getUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || "null"); }
  catch { return null; }
}
export function setUser(u) { localStorage.setItem(USER_KEY, JSON.stringify(u)); }

// ── Initialization ──────────────────────────────────────────────────────────

/**
 * Call once at plugin startup. Listens for the "hc:transsion-authed" event
 * dispatched by settings.js after a successful email-code login, then:
 *   - stores token + user in localStorage
 *   - re-dispatches "hc:forum-ready" so forum.js can show the tab
 */
export function initAuth() {
  document.addEventListener("hc:transsion-authed", (ev) => {
    const { accessToken, email, displayName } = ev.detail || {};
    if (!accessToken) return;
    setToken(accessToken);
    setUser({ email, displayName });
    document.dispatchEvent(new CustomEvent("hc:forum-ready", {
      detail: { email, displayName },
    }));
  });
}

/**
 * True when there is a persisted token (i.e. user is considered logged in).
 * The token may be expired — api.js handles 401 by calling clearToken().
 */
export function isAuthed() { return Boolean(getToken()); }
