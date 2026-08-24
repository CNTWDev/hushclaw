/**
 * shell.js — persistent application-shell interactions.
 * Kept independent from chat boot so navigation remains usable while the
 * backend is connecting.
 */

const RAIL_KEY = "hushclaw.ui.app-rail-expanded";
const THREADS_WIDTH_KEY = "hushclaw.ui.threads-width";
const WORKBENCH_WIDTH_KEY = "hushclaw.ui.workbench-width";
const body = document.body;
const toggle = document.getElementById("btn-toggle-app-rail");

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

function readStoredNumber(key, fallback) {
  try {
    const value = Number(localStorage.getItem(key));
    return Number.isFinite(value) && value > 0 ? value : fallback;
  } catch {
    return fallback;
  }
}

function applyColumnWidth(variable, key, value, min, max, handle) {
  const next = Math.round(clamp(value, min, max));
  document.documentElement.style.setProperty(variable, `${next}px`);
  handle?.setAttribute("aria-valuemin", String(min));
  handle?.setAttribute("aria-valuemax", String(max));
  handle?.setAttribute("aria-valuenow", String(next));
  try { localStorage.setItem(key, String(next)); } catch {}
  return next;
}

function wireColumnResize({ handleId, variable, key, initial, min, max, direction = 1 }) {
  const handle = document.getElementById(handleId);
  if (!handle) return;
  let current = applyColumnWidth(variable, key, readStoredNumber(key, initial), min, max, handle);
  let startX = 0;
  let startWidth = current;
  let frame = 0;
  let latestX = 0;

  const commitPointer = () => {
    frame = 0;
    current = applyColumnWidth(
      variable, key, startWidth + ((latestX - startX) * direction), min, max, handle,
    );
  };
  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    startX = latestX = event.clientX;
    startWidth = current;
    handle.setPointerCapture(event.pointerId);
    handle.classList.add("is-dragging");
    body.classList.add("is-resizing-columns");
  });
  handle.addEventListener("pointermove", (event) => {
    if (!handle.hasPointerCapture(event.pointerId)) return;
    latestX = event.clientX;
    if (!frame) frame = requestAnimationFrame(commitPointer);
  });
  const finish = (event) => {
    if (!handle.hasPointerCapture(event.pointerId)) return;
    if (frame) {
      cancelAnimationFrame(frame);
      commitPointer();
    }
    handle.releasePointerCapture(event.pointerId);
    handle.classList.remove("is-dragging");
    body.classList.remove("is-resizing-columns");
  };
  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);
  handle.addEventListener("keydown", (event) => {
    const delta = event.key === "ArrowRight" ? 16 : event.key === "ArrowLeft" ? -16 : 0;
    if (!delta && event.key !== "Home") return;
    event.preventDefault();
    current = applyColumnWidth(
      variable, key, event.key === "Home" ? initial : current + delta * direction,
      min, max, handle,
    );
  });
}

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

wireColumnResize({
  handleId: "sessions-resize-handle",
  variable: "--threads-drawer-w",
  key: THREADS_WIDTH_KEY,
  initial: 276,
  min: 220,
  max: 420,
});

wireColumnResize({
  handleId: "workbench-resize-handle",
  variable: "--workbench-w",
  key: WORKBENCH_WIDTH_KEY,
  initial: 360,
  min: 300,
  max: 520,
  direction: -1,
});
