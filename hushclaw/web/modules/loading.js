/**
 * loading.js — shared loading markup for a consistent WebUI experience.
 */

function _esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Build a unified loading block that reuses startup-style visuals.
 *
 * @param {{
 *   status?: string,
 *   hint?: string,
 *   compact?: boolean,
 *   height?: number,
 * }} opts
 */
export function renderLoadingMarkup(opts = {}) {
  const status = opts.status || "Loading…";
  const hint = opts.hint || "";
  const compact = Boolean(opts.compact);
  const height = Number(opts.height || 0);
  const cls = compact ? "app-loading app-loading--compact" : "app-loading";
  const style = height > 0 ? ` style="height:${height}px"` : "";
  return `
    <div class="${cls}"${style}>
      <div class="app-loading-card">
        <div class="app-loading-status">${_esc(status)}</div>
        <div class="app-loading-progress-track">
          <div class="app-loading-progress-bar"></div>
        </div>
        ${hint ? `<div class="app-loading-hint">${_esc(hint)}</div>` : ""}
      </div>
    </div>`;
}

