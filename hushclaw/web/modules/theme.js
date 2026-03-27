/**
 * theme.js — UI theme mode controller (auto/light/dark).
 */

import { wizard } from "./state.js";

export const THEME_STORAGE_KEY = "hushclaw.ui.theme-mode";
export const THEME_MODES = ["auto", "light", "dark"];

let _mode = "auto";
let _mql = null;

function isValidMode(v) {
  return THEME_MODES.includes(v);
}

function getStoredMode() {
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY);
    return isValidMode(v) ? v : "auto";
  } catch {
    return "auto";
  }
}

function resolveTheme(mode) {
  if (mode === "light" || mode === "dark") return mode;
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  return prefersDark ? "dark" : "light";
}

function applyResolvedTheme(mode, resolved) {
  const root = document.documentElement;
  root.dataset.themeMode = mode;
  root.dataset.theme = resolved;
  root.style.colorScheme = resolved;
}

function _handleSystemThemeChange() {
  if (_mode !== "auto") return;
  applyResolvedTheme(_mode, resolveTheme(_mode));
}

function ensureMediaListener() {
  if (!window.matchMedia) return;
  if (!_mql) _mql = window.matchMedia("(prefers-color-scheme: dark)");
  if (_mql._hushclawBound) return;
  const handler = _handleSystemThemeChange;
  if (_mql.addEventListener) _mql.addEventListener("change", handler);
  else _mql.addListener(handler);
  _mql._hushclawBound = true;
}

export function getThemeMode() {
  return _mode;
}

export function applyThemeMode(mode, { persist = true } = {}) {
  const nextMode = isValidMode(mode) ? mode : "auto";
  _mode = nextMode;
  wizard.themeMode = nextMode;
  applyResolvedTheme(nextMode, resolveTheme(nextMode));
  if (persist) {
    try { localStorage.setItem(THEME_STORAGE_KEY, nextMode); } catch {}
  }
  return nextMode;
}

export function initTheme() {
  ensureMediaListener();
  applyThemeMode(getStoredMode(), { persist: false });
}

export function bindThemeControls(scope = document) {
  const radios = scope.querySelectorAll('input[name="ui-theme-mode"]');
  if (!radios.length) return;
  radios.forEach((radio) => {
    radio.checked = radio.value === _mode;
    radio.addEventListener("change", () => {
      if (radio.checked) applyThemeMode(radio.value);
    });
  });
}
