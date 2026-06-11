/**
 * panels/files.js — Right-sidebar file knowledge base panel.
 * Lists all files in the upload directory, paginated, time-sorted descending.
 * - Double-click .md file → markdown preview dialog
 * - Drag .md file onto sidebar → upload + index into knowledge base
 * - Attach button per item → add existing file to current message
 * - Delete button per item → hide logical file entry
 */

import { state, send, escHtml, showToast } from "../state.js";
import { markdownSurfaceClass, setMarkdownContent, unmountMarkdown } from "../markdown.js";
import { openDialog, openConfirm, closeModal } from "../modal.js";
import { uploadFile, addExistingAttachment } from "../events/upload.js";
import { resolveFileUrl } from "../http.js";

const _COLLAPSED_KEY = "hushclaw.ui.files-sidebar-collapsed";
const _LIMIT = 20;

let _collapsed = false;
let _offset = 0;
let _total = 0;
let _sourceFilter = "all"; // "all" | "upload" | "generated"
let _query = "";
let _searchTimer = null;
let _resizeBound = false;
let _dismissBound = false;
const _unseenGeneratedFiles = new Map();

// ── Init ──────────────────────────────────────────────────────────────────────

export function initFilesSidebar() {
  const _savedCollapsed = localStorage.getItem(_COLLAPSED_KEY);
  _applyCollapsed(_savedCollapsed !== null ? _savedCollapsed === "true" : true);

  document.getElementById("btn-toggle-files-sidebar")?.addEventListener("click", toggleFilesSidebar);
  document.getElementById("btn-toggle-files-inline")?.addEventListener("click", toggleFilesSidebar);
  document.getElementById("btn-refresh-files")?.addEventListener("click", refreshFilesList);
  document.getElementById("files-sidebar")?.addEventListener("pointerdown", (ev) => {
    ev.stopPropagation();
  });
  if (!_dismissBound) {
    document.addEventListener("pointerdown", _handleOutsidePointerDown);
    document.addEventListener("keydown", _handleKeydown);
    _dismissBound = true;
  }
  if (!_resizeBound) {
    window.addEventListener("resize", _syncToggleButtons);
    _resizeBound = true;
  }

  _initDragDrop();
}

export function toggleFilesSidebar(forceCollapsed) {
  if (typeof forceCollapsed === "boolean") {
    _applyCollapsed(forceCollapsed);
    return;
  }
  _applyCollapsed(!_collapsed);
}

function _applyCollapsed(collapsed) {
  _collapsed = !!collapsed;
  document.body.classList.toggle("files-sidebar-collapsed", _collapsed);
  if (!_collapsed) _unseenGeneratedFiles.clear();
  _syncToggleButtons();
  try { localStorage.setItem(_COLLAPSED_KEY, _collapsed ? "true" : "false"); } catch {}
}

function _artifactKey(item) {
  return String(item?.file_id || item?.artifact_id || item?.url || item?.name || "").trim();
}

function _ensureInlineBadge(button) {
  if (!button) return null;
  let badge = button.querySelector(".chat-context-badge");
  if (!badge) {
    badge = document.createElement("span");
    badge.className = "chat-context-badge hidden";
    badge.setAttribute("aria-hidden", "true");
    button.appendChild(badge);
  }
  return badge;
}

function _syncToggleButtons() {
  const btn = document.getElementById("btn-toggle-files-sidebar");
  if (btn) {
    const label = _collapsed ? "Open" : "Close";
    const title = _collapsed ? "Open files drawer" : "Close files drawer";
    btn.textContent = label;
    btn.title = title;
    btn.setAttribute("aria-label", title);
    btn.dataset.state = _collapsed ? "closed" : "open";
  }
  const inlineBtn = document.getElementById("btn-toggle-files-inline");
  if (inlineBtn) {
    inlineBtn.classList.remove("hidden");
    inlineBtn.classList.toggle("active", !_collapsed);
    inlineBtn.title = _collapsed ? "Open files drawer" : "Close files drawer";
    inlineBtn.setAttribute("aria-label", inlineBtn.title);
    inlineBtn.setAttribute("aria-expanded", _collapsed ? "false" : "true");
    inlineBtn.setAttribute("aria-controls", "files-sidebar");
    const badge = _ensureInlineBadge(inlineBtn);
    const unseen = _unseenGeneratedFiles.size;
    if (badge) {
      badge.textContent = unseen > 9 ? "9+" : String(unseen || "");
      badge.classList.toggle("hidden", unseen <= 0);
    }
    inlineBtn.dataset.unseenCount = unseen ? String(unseen) : "";
  }
}

export function noteGeneratedArtifacts(artifacts = [], { showToast: shouldToast = true } = {}) {
  const fresh = [];
  for (const artifact of Array.isArray(artifacts) ? artifacts : []) {
    const key = _artifactKey(artifact);
    const url = String(artifact?.url || "").trim();
    if (!key || !url.startsWith("/files/")) continue;
    if (_unseenGeneratedFiles.has(key)) continue;
    const normalized = {
      file_id: String(artifact?.file_id || artifact?.artifact_id || "").trim(),
      artifact_id: String(artifact?.artifact_id || artifact?.file_id || "").trim(),
      url,
      name: String(artifact?.name || url.split("/").filter(Boolean).pop() || "file").trim() || "file",
      kind: String(artifact?.kind || "file").trim() || "file",
    };
    if (_collapsed) _unseenGeneratedFiles.set(key, normalized);
    fresh.push(normalized);
  }
  if (!fresh.length) {
    _syncToggleButtons();
    return;
  }
  _syncToggleButtons();
  if (shouldToast) {
    const message = fresh.length === 1
      ? `New file ready: ${fresh[0].name}`
      : `${fresh.length} new files ready`;
    showToast(message, "info");
  }
}

export function markGeneratedArtifactsSeen(artifacts = []) {
  if (!artifacts || (Array.isArray(artifacts) && artifacts.length === 0)) {
    _unseenGeneratedFiles.clear();
    _syncToggleButtons();
    return;
  }
  for (const artifact of Array.isArray(artifacts) ? artifacts : [artifacts]) {
    const key = _artifactKey(artifact);
    if (key) _unseenGeneratedFiles.delete(key);
  }
  _syncToggleButtons();
}

function _handleOutsidePointerDown(ev) {
  if (_collapsed) return;
  const target = ev.target;
  if (!(target instanceof Element)) return;
  if (target.closest("#files-sidebar")) return;
  if (target.closest("#btn-toggle-files-inline")) return;
  if (target.closest(".app-modal, .app-modal-card, [role='dialog']")) return;
  _applyCollapsed(true);
}

function _handleKeydown(ev) {
  if (ev.key !== "Escape" || _collapsed) return;
  _applyCollapsed(true);
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
  if (_query) msg.query = _query;
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

  let searchBar = document.getElementById("files-search-bar");
  if (!searchBar) {
    searchBar = document.createElement("div");
    searchBar.id = "files-search-bar";
    searchBar.className = "files-search-bar";
    searchBar.innerHTML = `
      <input id="files-search-input" class="files-search-input" type="search"
        placeholder="Search files" aria-label="Search files">
      <span id="files-search-state" class="files-search-state"></span>
      <button id="files-search-clear" class="files-search-clear" title="Clear search"
        aria-label="Clear search">Clear</button>
    `;
    list.parentElement?.insertBefore(searchBar, list);
    const createdInput = searchBar.querySelector("#files-search-input");
    const createdClear = searchBar.querySelector("#files-search-clear");
    createdInput?.addEventListener("input", () => {
      const next = createdInput.value.trim();
      if (next === _query) return;
      _query = next;
      _offset = 0;
      if (createdClear) createdClear.disabled = !_query;
      if (_searchTimer) window.clearTimeout(_searchTimer);
      _searchTimer = window.setTimeout(() => {
        _searchTimer = null;
        _sendListFiles();
      }, 200);
    });
    createdClear?.addEventListener("click", () => {
      if (!_query) return;
      _query = "";
      _offset = 0;
      if (createdInput) createdInput.value = "";
      createdClear.disabled = true;
      if (_searchTimer) {
        window.clearTimeout(_searchTimer);
        _searchTimer = null;
      }
      _sendListFiles();
    });
  }
  const searchInput = document.getElementById("files-search-input");
  const searchClear = document.getElementById("files-search-clear");
  const searchState = document.getElementById("files-search-state");
  if (searchInput && document.activeElement !== searchInput && searchInput.value !== _query) {
    searchInput.value = _query;
  }
  if (searchClear) searchClear.disabled = !_query;
  if (searchState) {
    searchState.textContent = _query ? `${data.total || 0} match${Number(data.total || 0) === 1 ? "" : "es"}` : "";
  }

  const items = data.items || [];

  if (!items.length && _offset === 0) {
    list.innerHTML = _query
      ? `<div class="files-empty">No files match "${escHtml(_query)}"</div>`
      : '<div class="files-empty">Drop a .md file here to add it to the knowledge base</div>';
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
    const updatedStr = _fmtRelTime(item.modified || item.updated || item.created);
    const updatedTitle = _fmtAbsTime(item.modified || item.updated || item.created);
    const ext = _extLabel(item.name);
    const badge = item.source === "generated"
      ? `<span class="file-badge file-badge--gen" title="AI 生成">生成</span>`
      : item.indexed
        ? `<span class="file-badge file-badge--indexed" title="已加入知识库">知识库</span>`
        : "";
    const previewType = isMarkdown ? "md" : isHtml ? "html" : isPdf ? "pdf" : isImage ? "image" : "";
    const isUnseen = _unseenGeneratedFiles.has(_artifactKey(item));
    return `<div class="file-item${isPreviewable ? " file-item--preview" : " file-item--no-preview"}${isUnseen ? " file-item--new" : ""}"
              data-url="${escHtml(item.url)}"
              data-name="${escHtml(item.name)}"
              data-file-id="${escHtml(item.file_id || "")}"
              data-filename="${escHtml(item.filename)}"
              data-preview-type="${previewType}"
              title="${isPreviewable ? "Double-click to preview" : item.name}">
      <div class="file-item-ext">${escHtml(ext)}</div>
      <div class="file-item-info">
        <div class="file-item-name">${escHtml(item.name)}${badge}</div>
        <div class="file-item-meta" title="Last updated: ${escHtml(updatedTitle)}">${escHtml(sizeStr)} · Updated ${escHtml(updatedStr)}</div>
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
      markGeneratedArtifactsSeen({
        file_id: el.dataset.fileId || "",
        url: el.dataset.url || "",
        name: el.dataset.name || "",
      });
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
      markGeneratedArtifactsSeen({
        file_id: btn.dataset.fileId || "",
        url: itemEl.dataset.url || "",
        name: itemEl.dataset.name || "",
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
  markGeneratedArtifactsSeen({ file_id: data.file_id || "" });
  refreshFilesList();
}

// ── Preview ───────────────────────────────────────────────────────────────────

function _closePreviewAction() {
  return {
    label: "Close",
    secondary: true,
    onClick: closeModal,
  };
}

function _downloadBlob(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename || "download";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 0);
}

async function _downloadFile(item, mimeType = "application/octet-stream") {
  const apiKey = state.apiKey || "";
  const url = resolveFileUrl(item.url, apiKey);
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    _downloadBlob(blob.type ? blob : blob.slice(0, blob.size, mimeType), item.name);
  } catch (e) {
    showToast(`Download failed: ${e.message}`, "error");
  }
}

function _previewHtml(item) {
  const apiKey = state.apiKey || "";
  const url = resolveFileUrl(item.url, apiKey);
  openDialog({
    title: item.name,
    html: `<div class="file-preview-frame file-preview-html"><iframe src="${escHtml(url)}" sandbox="allow-scripts allow-same-origin" loading="lazy"></iframe></div>`,
    actions: [
      {
        label: "Download",
        secondary: true,
        onClick: () => _downloadFile(item, "text/html"),
      },
      _closePreviewAction(),
    ],
    closeOnBackdrop: true,
    wideCard: true,
  });
}

function _previewPdf(item) {
  const apiKey = state.apiKey || "";
  const url = resolveFileUrl(item.url, apiKey);
  openDialog({
    title: item.name,
    html: `<div class="file-preview-frame file-preview-pdf"><iframe src="${escHtml(url)}" loading="lazy"></iframe></div>`,
    actions: [_closePreviewAction()],
    closeOnBackdrop: true,
    wideCard: true,
  });
}

function _previewImage(item) {
  const apiKey = state.apiKey || "";
  const url = resolveFileUrl(item.url, apiKey);
  openDialog({
    title: item.name,
    html: `<div class="file-preview-frame file-preview-image"><img src="${escHtml(url)}" alt="${escHtml(item.name)}"></div>`,
    actions: [_closePreviewAction()],
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
  const previewId = `file-md-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  openDialog({
    title: item.name,
    html: `<div id="${previewId}" class="file-preview-body ${markdownSurfaceClass("file")}"></div>`,
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
          _downloadBlob(new Blob([text], { type: "text/markdown" }), item.name);
        },
      },
      _closePreviewAction(),
    ],
    closeOnBackdrop: true,
    cardClass: "app-modal-card--document",
    onOpen: () => {
      const previewEl = document.getElementById(previewId);
      setMarkdownContent(previewEl, text, { surface: "file", className: "file-preview-body" });
    },
    onClose: () => {
      const previewEl = document.getElementById(previewId);
      unmountMarkdown(previewEl);
    },
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function _fmtRelTime(ts) {
  if (!ts) return "unknown";
  const diff = Math.floor(Date.now() / 1000) - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

function _fmtAbsTime(ts) {
  if (!ts) return "unknown";
  return new Date(ts * 1000).toLocaleString();
}

function _extLabel(name) {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toUpperCase().slice(0, 4) : "FILE";
}
