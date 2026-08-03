/**
 * shell.js — persistent application-shell interactions.
 * Kept independent from chat boot so navigation remains usable while the
 * backend is connecting.
 */

const RAIL_KEY = "hushclaw.ui.app-rail-expanded";
const body = document.body;
const toggle = document.getElementById("btn-toggle-app-rail");

function applyRailState(expanded) {
  body.classList.toggle("app-rail-expanded", expanded);
  if (!toggle) return;
  toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
  toggle.setAttribute("aria-label", expanded ? "Collapse navigation" : "Expand navigation");
  toggle.title = expanded ? "Collapse navigation" : "Expand navigation";
}

let initialExpanded = false;
try { initialExpanded = localStorage.getItem(RAIL_KEY) === "1"; } catch {}
applyRailState(initialExpanded && window.innerWidth > 760);

toggle?.addEventListener("click", () => {
  const next = !body.classList.contains("app-rail-expanded");
  applyRailState(next);
  try { localStorage.setItem(RAIL_KEY, next ? "1" : "0"); } catch {}
});

document.getElementById("btn-new-session-sidebar")?.addEventListener("click", () => {
  document.getElementById("btn-new-session")?.click();
  document.getElementById("input")?.focus();
});

window.addEventListener("resize", () => {
  if (window.innerWidth <= 760 && body.classList.contains("app-rail-expanded")) {
    applyRailState(false);
  }
});
