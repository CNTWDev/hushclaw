/**
 * theme.js — UI theme controller.
 *
 * HushClaw now has one product design system. The only user-facing dimension
 * is brightness: "auto" | "light" | "dark".
 *
 * HTML contract:
 *   <html data-theme="vector" data-mode="dark">
 *
 * CSS selectors:
 *   :root[data-theme="vector"][data-mode="dark"] { … }
 */

import { wizard } from "./state.js";

// ── Public constants ────────────────────────────────────────────────────────

export const THEMES = ["vector"];
export const MODES  = ["auto", "light", "dark"];

export const THEME_LABELS = {
  vector: "HushClaw",
};

export const THEME_STORAGE_KEY = "hushclaw.ui.theme";
export const MODE_STORAGE_KEY  = "hushclaw.ui.mode";

// Legacy key written by older versions — read once for migration, then drop.
const _LEGACY_MODE_KEY = "hushclaw.ui.theme-mode";

// ── Internal state ──────────────────────────────────────────────────────────

let _theme = "vector";
let _mode  = "auto";
let _mql   = null;

// ── Helpers ─────────────────────────────────────────────────────────────────

function isValidMode(v)  { return MODES.includes(v); }

function resolveMode(mode) {
  if (mode === "light" || mode === "dark") return mode;
  return (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches)
    ? "dark" : "light";
}

function applyToDOM(theme, resolvedMode) {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.dataset.mode  = resolvedMode;
  root.style.colorScheme = resolvedMode;
}

function _handleSystemChange() {
  if (_mode !== "auto") return;
  applyToDOM(_theme, resolveMode("auto"));
}

function ensureMediaListener() {
  if (!window.matchMedia) return;
  if (_mql) return;
  _mql = window.matchMedia("(prefers-color-scheme: dark)");
  if (_mql.addEventListener) _mql.addEventListener("change", _handleSystemChange);
  else _mql.addListener(_handleSystemChange);
}

function getStoredTheme() {
  try {
    // Theme choice was retired in the unified-design migration. Do not keep a
    // stale preference that could resurrect a removed palette on downgrade.
    localStorage.removeItem(THEME_STORAGE_KEY);
    return "vector";
  } catch (_e) { return "vector"; }
}

function getStoredMode() {
  try {
    // Migrate legacy key once
    const legacy = localStorage.getItem(_LEGACY_MODE_KEY);
    if (legacy && isValidMode(legacy)) {
      localStorage.setItem(MODE_STORAGE_KEY, legacy);
      localStorage.removeItem(_LEGACY_MODE_KEY);
      return legacy;
    }
    const v = localStorage.getItem(MODE_STORAGE_KEY);
    return isValidMode(v) ? v : "auto";
  } catch (_e) { return "auto"; }
}

// ── Public API ───────────────────────────────────────────────────────────────

/** Apply the fixed product theme. Kept as an API for existing modules. */
export function applyTheme(_themeName, { persist = true } = {}) {
  const next = "vector";
  _theme = next;
  wizard.theme = next;
  applyToDOM(_theme, resolveMode(_mode));
  if (persist) {
    try { localStorage.removeItem(THEME_STORAGE_KEY); } catch (_e) {}
  }
  return next;
}

/** Apply a brightness mode and persist to localStorage. */
export function applyMode(mode, { persist = true } = {}) {
  const next = isValidMode(mode) ? mode : "auto";
  _mode = next;
  wizard.themeMode = next;
  applyToDOM(_theme, resolveMode(next));
  if (persist) {
    try { localStorage.setItem(MODE_STORAGE_KEY, next); } catch (_e) {}
  }
  return next;
}

export function getTheme()     { return _theme; }
export function getThemeMode() { return _mode; }

/** Bind <input type="radio" name="ui-theme-mode"> controls. */
export function bindThemeControls(scope = document) {
  scope.querySelectorAll('input[name="ui-theme-mode"]').forEach((radio) => {
    radio.checked = radio.value === _mode;
    radio.addEventListener("change", () => {
      if (radio.checked) applyMode(radio.value);
    });
  });
}

/** Initialize both dimensions from localStorage (call once on page load). */
export function initTheme() {
  ensureMediaListener();
  applyTheme(getStoredTheme(), { persist: false });
  applyMode(getStoredMode(),   { persist: false });
}
