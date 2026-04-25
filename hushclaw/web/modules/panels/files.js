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

  _initDragDrop();
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
    list.innerHTML = '<div class="files-empty">拖入 .md 文件开始建立知识库</div>';
    if (pag) pag.innerHTML = "";
    return;
  }

  list.innerHTML = items.map(item => {
    const nameLower = item.name.toLowerCase();
    const isMarkdown = nameLower.endsWith(".md");
    const isHtml = nameLower.endsWith(".html") || nameLower.endsWith(".htm");
    const isPreviewable = isMarkdown || isHtml;
    const sizeStr = _fmtSize(item.size);
    const timeStr = _fmtRelTime(item.modified);
    const ext = _extLabel(item.name);
    return `<div class="file-item${isPreviewable ? " file-item--preview" : " file-item--no-preview"}"
              data-url="${escHtml(item.url)}"
              data-name="${escHtml(item.name)}"
              data-file-id="${escHtml(item.file_id || "")}"
              data-filename="${escHtml(item.filename)}"
              data-preview-type="${isMarkdown ? "md" : isHtml ? "html" : ""}"
              title="${isPreviewable ? "Double-click to preview" : item.name}">
      <div class="file-item-ext">${escHtml(ext)}</div>
      <div class="file-item-info">
        <div class="file-item-name">${escHtml(item.name)}</div>
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
      if (type === "html") {
        _previewHtml({ url: el.dataset.url, name: el.dataset.name });
      } else {
        _previewMarkdown({ url: el.dataset.url, name: el.dataset.name });
      }
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
      showToast(`已附加 ${itemEl.dataset.name}`, "info");
    });
  });

  list.querySelectorAll(".file-item-del").forEach(btn => {
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const fileId = btn.dataset.fileId;
      const itemEl = btn.closest(".file-item");
      const displayName = itemEl?.dataset.name || btn.dataset.filename || fileId;
      const ok = await openConfirm({
        title: "删除文件",
        message: `确认删除 "${displayName}"？此操作不可撤销。`,
        confirmText: "删除",
        cancelText: "取消",
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
    showToast("已加入知识库索引", "info");
  } else {
    showToast(`索引失败: ${data.error || "unknown"}`, "error");
  }
}

export function handleFileDeleted(data) {
  if (!data.ok) {
    showToast(`删除失败: ${data.error || "unknown"}`, "error");
    return;
  }
  refreshFilesList();
}

// ── Preview ───────────────────────────────────────────────────────────────────

function _previewHtml(item) {
  const apiKey = state.apiKey || "";
  const url = item.url + (apiKey ? (item.url.includes("?") ? "&" : "?") + "api_key=" + encodeURIComponent(apiKey) : "");
  openDialog({
    title: item.name,
    html: `<div class="file-preview-html"><iframe src="${escHtml(url)}" sandbox="allow-scripts allow-same-origin" loading="lazy"></iframe></div>`,
    actions: [],
    closeOnBackdrop: true,
    wideCard: true,
  });
}

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
