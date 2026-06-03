import { escHtml, send } from "../state.js";

function _controls() {
  return {
    query: document.getElementById("logs-search"),
    level: document.getElementById("logs-level"),
    limit: document.getElementById("logs-limit"),
    refresh: document.getElementById("btn-refresh-logs"),
    list: document.getElementById("logs-list"),
  };
}

function _formatTs(ts) {
  const n = Number(ts || 0);
  if (!n) return "";
  return new Date(n * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function refreshLogs() {
  const c = _controls();
  send({
    type: "get_logs",
    query: c.query?.value?.trim() || "",
    level: c.level?.value || "",
    limit: Number(c.limit?.value || 300),
  });
}

export function renderLogs(items = []) {
  const { list } = _controls();
  if (!list) return;
  if (!items.length) {
    list.innerHTML = '<div class="logs-empty">No matching logs in this server process.</div>';
    return;
  }
  list.innerHTML = items.map((item) => {
    const level = String(item.level || "INFO").toLowerCase();
    const msg = String(item.message || "");
    const exc = item.exc ? `\n${item.exc}` : "";
    return `
      <div class="log-row log-row--${escHtml(level)}">
        <span class="log-time">${escHtml(_formatTs(item.ts))}</span>
        <span class="log-level">${escHtml(item.level || "")}</span>
        <span class="log-logger">${escHtml(item.logger || "")}</span>
        <span class="log-message">${escHtml(msg + exc)}</span>
      </div>
    `;
  }).join("");
  list.scrollTop = list.scrollHeight;
}

let _bound = false;

export function initLogsPanel() {
  if (_bound) return;
  _bound = true;
  const c = _controls();
  c.refresh?.addEventListener("click", refreshLogs);
  c.level?.addEventListener("change", refreshLogs);
  c.limit?.addEventListener("change", refreshLogs);
  c.query?.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" || ev.isComposing) return;
    ev.preventDefault();
    refreshLogs();
  });
}
