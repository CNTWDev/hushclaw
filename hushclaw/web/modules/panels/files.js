/**
 * panels/files.js — Right-sidebar file knowledge base panel.
 * Lists all files in the upload directory, paginated, time-sorted descending.
 * - Double-click .md file → markdown preview dialog
 * - Drag .md file onto sidebar → upload + index into knowledge base
 * - Attach button per item → add existing file to current message
 * - Delete button per item → hide logical file entry
 */

import { state, send, escHtml, showToast } from "../state.js";
import { renderMarkdown } from "../markdown.js";
import { openDialog, openConfirm } from "../modal.js";
import { uploadFile, addExistingAttachment } from "../events/upload.js";
import { resolveFileUrl } from "../http.js";

const _COLLAPSED_KEY = "hushclaw.ui.files-sidebar-collapsed";
const _LIMIT = 20;

let _collapsed = false;
let _offset = 0;
let _total = 0;
let _sourceFilter = "all"; // "all" | "upload" | "generated"

// ── Init ──────────────────────────────────────────────────────────────────────

export function initFilesSidebar() {
  _applyCollapsed(localStorage.getItem(_COLLAPSED_KEY) === "true");

  document.getElementById("btn-toggle-files-sidebar")?.addEventListener("click", toggleFilesSidebar);
  document.getElementById("btn-toggle-files-inline")?.addEventListener("click", toggleFilesSidebar);
  document.getElementById("btn-refresh-files")?.addEventListener("click", refreshFilesList);

  _initDragDrop();
}

export function toggleFilesSidebar() {
  _applyCollapsed(!_collapsed);
}

function _applyCollapsed(collapsed) {
  _collapsed = !!collapsed;
  document.body.classList.toggle("files-sidebar-collapsed", _collapsed);
  const btn = document.getElementById("btn-toggle-files-sidebar");
  if (btn) {
    btn.textContent = _collapsed ? "⟩" : "⟨";
    btn.title = _collapsed ? "Expand files panel" : "Collapse files panel";
  }
  const inlineBtn = document.getElementById("btn-toggle-files-inline");
  if (inlineBtn) inlineBtn.classList.toggle("hidden", !_collapsed);
  try { localStorage.setItem(_COLLAPSED_KEY, _collapsed ? "true" : "false"); } catch {}
}

// ── Drag & drop upload ────────────────────────────────────────────────────────

function _initDragDrop() {
  const sidebar = document.getElementById("files-sidebar");
  if (!sidebar) return;

  sidebar.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    sidebar.classList.add("files-drag-over");
  });
  sidebar.addEventListener("dragleave", (ev) => {
    if (!sidebar.contains(ev.relatedTarget)) {
      sidebar.classList.remove("files-drag-over");
    }
  });
  sidebar.addEventListener("drop", async (ev) => {
    ev.preventDefault();
    sidebar.classList.remove("files-drag-over");
    const files = Array.from(ev.dataTransfer?.files || []);
    if (!files.length) return;

    for (const file of files) {
      const isMd = file.name.toLowerCase().endsWith(".md");
      await _uploadAndOptionallyIndex(file, isMd);
    }
    refreshFilesList();
  });
}

async function _uploadAndOptionallyIndex(file, index) {
  const result = await uploadFile(file);
  if (!result?.ok) {
    showToast(`Upload failed: ${result?.error || "unknown error"}`, "error");
    return;
  }
  if (index) {
    send({ type: "ingest_file", file_id: result.file_id });
  }
}

// ── Data fetching ─────────────────────────────────────────────────────────────

export function refreshFilesList() {
  _offset = 0;
  _sendListFiles();
}

function _loadPage(offset) {
  _offset = offset;
  _sendListFiles();
}

function _sendListFiles() {
  const msg = { type: "list_files", offset: _offset, limit: _LIMIT };
  if (_sourceFilter !== "all") msg.source = _sourceFilter;
  send(msg);
}

// ── Render ────────────────────────────────────────────────────────────────────

export function renderFiles(data) {
  _total = data.total ?? 0;
  _offset = data.offset ?? 0;

  const list = document.getElementById("files-list");
  const pag = document.getElementById("files-pagination");
  if (!list) return;

  // ── Tab bar ──────────────────────────────────────────────────────────────
  let tabBar = document.getElementById("files-tab-bar");
  if (!tabBar) {
    tabBar = document.createElement("div");
    tabBar.id = "files-tab-bar";
    tabBar.className = "files-tab-bar";
    list.parentElement?.insertBefore(tabBar, list);
  }
  const tabs = [
    { key: "all", label: "全部" },
    { key: "upload", label: "上传" },
    { key: "generated", label: "生成" },
  ];
  tabBar.innerHTML = tabs.map(t =>
    `<button class="files-tab${_sourceFilter === t.key ? " files-tab--active" : ""}" data-source="${t.key}">${t.label}</button>`
  ).join("");
  tabBar.querySelectorAll(".files-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      _sourceFilter = btn.dataset.source;
      _offset = 0;
      _sendListFiles();
    });
  });
  // ─────────────────────────────────────────────────────────────────────────

  const items = data.items || [];

  if (!items.length && _offset === 0) {
    list.innerHTML = '<div class="files-empty">Drop a .md file here to add it to the knowledge base</div>';
    if (pag) pag.innerHTML = "";
    return;
  }

  list.innerHTML = items.map(item => {
    const nameLower = item.name.toLowerCase();
    const isMarkdown = nameLower.endsWith(".md");
    const isHtml = nameLower.endsWith(".html") || nameLower.endsWith(".htm");
    const isPdf = nameLower.endsWith(".pdf");
    const isImage = /\.(jpe?g|png|gif|webp|svg|bmp|ico)$/.test(nameLower);
    const isPreviewable = isMarkdown || isHtml || isPdf || isImage;
    const sizeStr = _fmtSize(item.size);
    const timeStr = _fmtRelTime(item.modified);
    const ext = _extLabel(item.name);
    const badge = item.source === "generated"
      ? `<span class="file-badge file-badge--gen" title="AI 生成">生成</span>`
      : item.indexed
        ? `<span class="file-badge file-badge--indexed" title="已加入知识库">知识库</span>`
        : "";
    const previewType = isMarkdown ? "md" : isHtml ? "html" : isPdf ? "pdf" : isImage ? "image" : "";
    return `<div class="file-item${isPreviewable ? " file-item--preview" : " file-item--no-preview"}"
              data-url="${escHtml(item.url)}"
              data-name="${escHtml(item.name)}"
              data-file-id="${escHtml(item.file_id || "")}"
              data-filename="${escHtml(item.filename)}"
              data-preview-type="${previewType}"
              title="${isPreviewable ? "Double-click to preview" : item.name}">
      <div class="file-item-ext">${escHtml(ext)}</div>
      <div class="file-item-info">
        <div class="file-item-name">${escHtml(item.name)}${badge}</div>
        <div class="file-item-meta">${escHtml(sizeStr)} · ${escHtml(timeStr)}</div>
      </div>
      <div class="file-item-actions">
        <button class="file-item-attach" data-file-id="${escHtml(item.file_id || "")}" title="Attach file">Attach</button>
        <button class="file-item-del" data-file-id="${escHtml(item.file_id || "")}" data-filename="${escHtml(item.filename)}" title="Delete file">✕</button>
      </div>
    </div>`;
  }).join("");

  list.querySelectorAll(".file-item--preview").forEach(el => {
    el.addEventListener("dblclick", (ev) => {
      if (ev.target.classList.contains("file-item-del")) return;
      const type = el.dataset.previewType;
      const item = { url: el.dataset.url, name: el.dataset.name };
      if (type === "html") _previewHtml(item);
      else if (type === "pdf") _previewPdf(item);
      else if (type === "image") _previewImage(item);
      else _previewMarkdown(item);
    });
  });

  list.querySelectorAll(".file-item-attach").forEach(btn => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const itemEl = btn.closest(".file-item");
      addExistingAttachment({
        file_id: btn.dataset.fileId,
        name: itemEl.dataset.name,
        url: itemEl.dataset.url,
      });
      showToast(`Attached ${itemEl.dataset.name}`, "info");
    });
  });

  list.querySelectorAll(".file-item-del").forEach(btn => {
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const fileId = btn.dataset.fileId;
      const itemEl = btn.closest(".file-item");
      const displayName = itemEl?.dataset.name || btn.dataset.filename || fileId;
      const ok = await openConfirm({
        title: "Delete file",
        message: `Delete "${displayName}"? This cannot be undone.`,
        confirmText: "Delete",
        cancelText: "Cancel",
        dangerConfirm: true,
      });
      if (!ok) return;
      send({ type: "delete_file", file_id: fileId });
    });
  });

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

// ── Server response handlers ──────────────────────────────────────────────────

export function handleFileIngested(data) {
  if (data.ok) {
    showToast("Added to knowledge base index", "info");
  } else {
    showToast(`Index failed: ${data.error || "unknown"}`, "error");
  }
}

export function handleFileDeleted(data) {
  if (!data.ok) {
    showToast(`Delete failed: ${data.error || "unknown"}`, "error");
    return;
  }
  refreshFilesList();
}

// ── Preview ───────────────────────────────────────────────────────────────────

function _previewHtml(item) {
  const apiKey = state.apiKey || "";
  const url = resolveFileUrl(item.url, apiKey);
  openDialog({
    title: item.name,
    html: `<div class="file-preview-html"><iframe src="${escHtml(url)}" sandbox="allow-scripts allow-same-origin" loading="lazy"></iframe></div>`,
    actions: [],
    closeOnBackdrop: true,
    wideCard: true,
  });
}

function _previewPdf(item) {
  const apiKey = state.apiKey || "";
  const url = resolveFileUrl(item.url, apiKey);
  openDialog({
    title: item.name,
    html: `<div class="file-preview-pdf"><iframe src="${escHtml(url)}" loading="lazy"></iframe></div>`,
    actions: [],
    closeOnBackdrop: true,
    wideCard: true,
  });
}

function _previewImage(item) {
  const apiKey = state.apiKey || "";
  const url = resolveFileUrl(item.url, apiKey);
  openDialog({
    title: item.name,
    html: `<div class="file-preview-image"><img src="${escHtml(url)}" alt="${escHtml(item.name)}"></div>`,
    actions: [],
    closeOnBackdrop: true,
  });
}

async function _previewMarkdown(item) {
  const apiKey = state.apiKey || "";
  const url = resolveFileUrl(item.url, apiKey);
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
    html: `<div class="file-preview-body markdown-body">${html}</div>`,
    actions: [
      {
        label: "Copy",
        secondary: true,
        onClick: () => {
          navigator.clipboard.writeText(text).then(
            () => showToast("Copied to clipboard", "info"),
            () => showToast("Copy failed", "error"),
          );
        },
      },
      {
        label: "Download",
        secondary: true,
        onClick: () => {
          const blob = new Blob([text], { type: "text/markdown" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = item.name;
          a.click();
          URL.revokeObjectURL(a.href);
        },
      },
    ],
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
