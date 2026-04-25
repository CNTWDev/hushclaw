/**
 * panels/files.js — Right-sidebar file knowledge base panel.
 * Lists all files in the upload directory, paginated, time-sorted descending.
 * Double-clicking a .md file opens a markdown preview dialog.
 */

import { state, send, escHtml, showToast } from "../state.js";
import { renderMarkdown } from "../markdown.js";
import { openDialog } from "../modal.js";

const _COLLAPSED_KEY = "hushclaw.ui.files-sidebar-collapsed";
const _LIMIT = 20;

let _offset = 0;
let _total = 0;

// ── Init ──────────────────────────────────────────────────────────────────────

export function initFilesSidebar() {
  const collapsed = localStorage.getItem(_COLLAPSED_KEY) === "true";
  document.body.classList.toggle("files-sidebar-collapsed", collapsed);
  _updateToggleBtn(collapsed);

  document.getElementById("btn-toggle-files-sidebar")?.addEventListener("click", toggleFilesSidebar);
  document.getElementById("btn-refresh-files")?.addEventListener("click", refreshFilesList);

  refreshFilesList();
}

export function toggleFilesSidebar() {
  const collapsed = document.body.classList.toggle("files-sidebar-collapsed");
  localStorage.setItem(_COLLAPSED_KEY, collapsed);
  _updateToggleBtn(collapsed);
}

function _updateToggleBtn(collapsed) {
  const btn = document.getElementById("btn-toggle-files-sidebar");
  if (!btn) return;
  btn.textContent = collapsed ? "⟨" : "⟩";
  btn.title = collapsed ? "Show files panel" : "Collapse files panel";
}

// ── Data fetching ─────────────────────────────────────────────────────────────

export function refreshFilesList() {
  _offset = 0;
  send({ type: "list_files", offset: 0, limit: _LIMIT });
}

function _loadPage(offset) {
  _offset = offset;
  send({ type: "list_files", offset, limit: _LIMIT });
}

// ── Render ────────────────────────────────────────────────────────────────────

export function renderFiles(data) {
  _total = data.total ?? 0;
  _offset = data.offset ?? 0;

  const list = document.getElementById("files-list");
  const pag = document.getElementById("files-pagination");
  if (!list) return;

  const items = data.items || [];

  if (!items.length && _offset === 0) {
    list.innerHTML = '<div class="files-empty">No files yet</div>';
    if (pag) pag.innerHTML = "";
    return;
  }

  list.innerHTML = items.map(item => {
    const isMarkdown = item.name.toLowerCase().endsWith(".md");
    const sizeStr = _fmtSize(item.size);
    const timeStr = _fmtRelTime(item.modified);
    const ext = _extLabel(item.name);
    return `<div class="file-item${isMarkdown ? " file-item--preview" : " file-item--no-preview"}"
              data-url="${escHtml(item.url)}"
              data-name="${escHtml(item.name)}"
              data-is-md="${isMarkdown}"
              title="${isMarkdown ? "Double-click to preview" : item.name}">
      <div class="file-item-ext">${escHtml(ext)}</div>
      <div class="file-item-info">
        <div class="file-item-name">${escHtml(item.name)}</div>
        <div class="file-item-meta">${escHtml(sizeStr)} · ${escHtml(timeStr)}</div>
      </div>
    </div>`;
  }).join("");

  // Bind double-click for markdown files
  list.querySelectorAll(".file-item--preview").forEach(el => {
    el.addEventListener("dblclick", () => {
      _previewMarkdown({ url: el.dataset.url, name: el.dataset.name });
    });
  });

  // Pagination
  if (pag) {
    const hasPrev = _offset > 0;
    const hasNext = data.has_more;
    if (!hasPrev && !hasNext) {
      pag.innerHTML = _total > 0
        ? `<span class="files-count">${_total} file${_total !== 1 ? "s" : ""}</span>`
        : "";
    } else {
      pag.innerHTML = `
        <button class="files-pag-btn" id="files-pag-prev" ${hasPrev ? "" : "disabled"}>‹ Prev</button>
        <span class="files-count">${_offset + 1}–${Math.min(_offset + _LIMIT, _total)} of ${_total}</span>
        <button class="files-pag-btn" id="files-pag-next" ${hasNext ? "" : "disabled"}>Next ›</button>
      `;
      document.getElementById("files-pag-prev")?.addEventListener("click", () => _loadPage(_offset - _LIMIT));
      document.getElementById("files-pag-next")?.addEventListener("click", () => _loadPage(_offset + _LIMIT));
    }
  }
}

// ── Preview ───────────────────────────────────────────────────────────────────

async function _previewMarkdown(item) {
  const apiKey = state.apiKey || "";
  const url = item.url + (apiKey ? (item.url.includes("?") ? "&" : "?") + "api_key=" + encodeURIComponent(apiKey) : "");
  let text;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    text = await res.text();
  } catch (e) {
    showToast(`Failed to load file: ${e.message}`, "error");
    return;
  }
  const html = renderMarkdown(text);
  openDialog({
    title: item.name,
    html: `<div class="file-preview-body">${html}</div>`,
    actions: [],
    closeOnBackdrop: true,
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function _fmtRelTime(ts) {
  const diff = Math.floor(Date.now() / 1000) - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

function _extLabel(name) {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toUpperCase().slice(0, 4) : "FILE";
}
