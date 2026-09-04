/**
 * panels/files.js — Right-sidebar file knowledge base panel.
 * Lists all files in the upload directory, paginated, time-sorted descending.
 * - Single-click a previewable file → preview dialog
 * - Drag .md file onto sidebar → upload + index into knowledge base
 * - Attach button per item → add existing file to current message
 * - Delete button per item → hide logical file entry
 */

import {
  state, els, send, escHtml, showToast, refreshWorkbenchVisibility,
  pushWorkbenchActivity, getCurrentSessionId, getWorkbenchPreviewState, setWorkbenchPreviewState,
  isWorkbenchPanelPreferredVisible, isWorkbenchPanelVisible, setWorkbenchPanelVisible, toggleWorkbenchPanel,
} from "../state.js";
import { markdownSurfaceClass, setMarkdownContent, unmountMarkdown } from "../markdown.js";
import { openConfirm, openDialog, closeModal } from "../modal.js";
import { uploadFile, addExistingAttachment } from "../events/upload.js";
import { resolveFileUrl } from "../http.js";

const _LIMIT = 20;

let _offset = 0;
let _total = 0;
let _cursor = "";
let _nextCursor = "";
let _cursorStack = [];
let _sourceFilter = "all"; // "all" | "upload" | "generated"
let _query = "";
let _minRating = 0;
let _sortMode = "recent";
let _tagFilters = [];
let _tagFacets = [];
let _visibleItemsById = new Map();
let _searchTimer = null;
let _resizeBound = false;
let _loadedOnce = false;
let _loadRequested = false;
const _unseenGeneratedFiles = new Map();
const _pendingGeneratedFileAlerts = new Map();
let _workbenchPreviewCleanup = null;
let _workbenchPreviewItem = null;
let _workbenchPreviewPinned = false;
const _AUTO_PREVIEWABLE_EXTS = new Set([".md", ".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"]);

// ── Init ──────────────────────────────────────────────────────────────────────

export function initFilesSidebar() {
  try {
    const legacy = localStorage.getItem("hushclaw.ui.files-sidebar-collapsed");
    if (legacy !== null && state._workbenchPanelPrefs?.files == null) {
      setWorkbenchPanelVisible("files", legacy !== "true");
    }
    localStorage.removeItem("hushclaw.ui.files-sidebar-collapsed");
  } catch {
    // ignore storage errors
  }
  _syncFilesPanelVisibility();

  document.getElementById("btn-toggle-files-sidebar")?.addEventListener("click", toggleFilesSidebar);
  document.getElementById("btn-toggle-files-inline")?.addEventListener("click", toggleFilesSidebar);
  document.getElementById("btn-refresh-files")?.addEventListener("click", refreshFilesList);
  document.getElementById("workbench-preview-close")?.addEventListener("click", closeWorkbenchPreview);
  document.getElementById("workbench-preview-pin")?.addEventListener("click", () => {
    _workbenchPreviewPinned = !_workbenchPreviewPinned;
    _syncWorkbenchPreviewHeader();
    _persistWorkbenchPreview();
  });
  document.addEventListener("hc:workbench-activity-action", (ev) => {
    const detail = ev instanceof CustomEvent ? ev.detail || {} : {};
    if (detail.actionType !== "preview_artifact") return;
    const artifact = detail.artifact || {};
    const item = {
      name: artifact.name || "",
      url: artifact.url || "",
      kind: artifact.kind || "",
      source: artifact.source || "",
    };
    if (item.name && item.url) {
      markGeneratedArtifactsSeen(item);
      _openPreviewByItem(item);
    }
  });
  document.addEventListener("hc:session-context-changed", (ev) => {
    const detail = ev instanceof CustomEvent ? ev.detail || {} : {};
    _restoreWorkbenchPreviewForSession(detail.sessionId || "");
  });
  if (!_resizeBound) {
    window.addEventListener("resize", _syncToggleButtons);
    _resizeBound = true;
  }

  _initDragDrop();
  ensureFilesListLoaded();
}

export function toggleFilesSidebar(forceCollapsed) {
  if (typeof forceCollapsed === "boolean") {
    setWorkbenchPanelVisible("files", !forceCollapsed);
    _syncFilesPanelVisibility();
    return;
  }
  toggleWorkbenchPanel("files");
  _syncFilesPanelVisibility();
}

function _syncFilesPanelVisibility() {
  const visible = isWorkbenchPanelVisible("files");
  const panel = document.getElementById("files-sidebar");
  panel?.classList.toggle("hidden", !visible);
  if (visible) _acknowledgeGeneratedArtifactAlerts();
  if (visible) ensureFilesListLoaded();
  _syncToggleButtons();
  refreshWorkbenchVisibility();
}

function _artifactKey(item) {
  return String(item?.file_id || item?.artifact_id || item?.url || item?.name || "").trim();
}

function _artifactAliases(item) {
  return new Set([
    item?.file_id,
    item?.artifact_id,
    item?.url,
    item?.name,
  ].map(value => String(value || "").trim()).filter(Boolean));
}

function _deleteMatchingArtifacts(map, item) {
  const aliases = _artifactAliases(item);
  if (!aliases.size) return;
  for (const [key, stored] of map.entries()) {
    const storedAliases = _artifactAliases(stored);
    if (aliases.has(key) || [...storedAliases].some(alias => aliases.has(alias))) {
      map.delete(key);
    }
  }
}

function _syncReadControls() {
  const markAll = document.getElementById("files-mark-all-read");
  if (markAll) markAll.classList.toggle("hidden", _unseenGeneratedFiles.size <= 0);
}

function _syncSeenRows(item = null) {
  const aliases = item ? _artifactAliases(item) : null;
  document.querySelectorAll("#files-list .file-item--new").forEach(row => {
    if (!aliases) {
      row.classList.remove("file-item--new");
      return;
    }
    const rowAliases = _artifactAliases({
      file_id: row.dataset.fileId || "",
      url: row.dataset.url || "",
      name: row.dataset.name || "",
    });
    if ([...rowAliases].some(alias => aliases.has(alias))) {
      row.classList.remove("file-item--new");
    }
  });
  _syncReadControls();
}

function _acknowledgeGeneratedArtifactAlerts() {
  _pendingGeneratedFileAlerts.clear();
  _syncToggleButtons();
}

function _isPreviewableName(name = "") {
  const lower = String(name || "").toLowerCase();
  const dot = lower.lastIndexOf(".");
  return dot >= 0 && _AUTO_PREVIEWABLE_EXTS.has(lower.slice(dot));
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
  const preferredVisible = isWorkbenchPanelPreferredVisible("files");
  const btn = document.getElementById("btn-toggle-files-sidebar");
  if (btn) {
    const label = preferredVisible ? "Hide" : "Show";
    const title = preferredVisible ? "Hide files panel" : "Show files panel";
    btn.textContent = label;
    btn.title = title;
    btn.setAttribute("aria-label", title);
    btn.dataset.state = preferredVisible ? "open" : "closed";
  }
  const inlineBtn = document.getElementById("btn-toggle-files-inline");
  if (inlineBtn) {
    inlineBtn.classList.toggle("active", preferredVisible);
    inlineBtn.title = preferredVisible ? "Hide files panel" : "Show files panel";
    inlineBtn.setAttribute("aria-label", inlineBtn.title);
    inlineBtn.setAttribute("aria-expanded", preferredVisible ? "true" : "false");
    inlineBtn.setAttribute("aria-controls", "files-sidebar");
    const badge = _ensureInlineBadge(inlineBtn);
    const unseen = _pendingGeneratedFileAlerts.size;
    if (badge) {
      badge.textContent = unseen > 9 ? "9+" : String(unseen || "");
      badge.classList.toggle("hidden", unseen <= 0);
    }
    inlineBtn.dataset.unseenCount = unseen ? String(unseen) : "";
  }
}

export function noteGeneratedArtifacts(artifacts = [], { showToast: shouldToast = true } = {}) {
  const panelVisible = isWorkbenchPanelVisible("files");
  const fresh = [];
  for (const artifact of Array.isArray(artifacts) ? artifacts : []) {
    const key = _artifactKey(artifact);
    const url = String(artifact?.preview_url || artifact?.entry_url || artifact?.url || "").trim();
    if (!key || !url.startsWith("/files/")) continue;
    if (_unseenGeneratedFiles.has(key)) continue;
    const normalized = {
      file_id: String(artifact?.file_id || artifact?.artifact_id || "").trim(),
      artifact_id: String(artifact?.artifact_id || artifact?.file_id || "").trim(),
      url,
      name: String(artifact?.name || url.split("/").filter(Boolean).pop() || "file").trim() || "file",
      kind: String(artifact?.kind || "file").trim() || "file",
      entry_name: String(artifact?.entry_name || url.split("/").filter(Boolean).pop() || "").trim(),
      artifact_type: String(artifact?.artifact_type || "").trim(),
    };
    _unseenGeneratedFiles.set(key, normalized);
    if (!panelVisible) _pendingGeneratedFileAlerts.set(key, normalized);
    fresh.push(normalized);
  }
  if (!fresh.length) {
    _syncToggleButtons();
    return;
  }
  for (const artifact of fresh) {
    pushWorkbenchActivity({
      level: "artifact",
      title: "Artifact ready",
      summary: artifact.name,
      meta: artifact.artifact_type || artifact.kind || "file",
      group: "results",
      actionType: "preview_artifact",
      artifactName: artifact.entry_name || artifact.name,
      artifactUrl: artifact.url,
      artifactKind: artifact.kind || "file",
      artifactSource: artifact.source || "generated",
    });
  }
  _syncToggleButtons();
  _syncReadControls();
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
    _pendingGeneratedFileAlerts.clear();
    _syncSeenRows();
    _syncToggleButtons();
    return;
  }
  for (const artifact of Array.isArray(artifacts) ? artifacts : [artifacts]) {
    _deleteMatchingArtifacts(_unseenGeneratedFiles, artifact);
    _deleteMatchingArtifacts(_pendingGeneratedFileAlerts, artifact);
    _syncSeenRows(artifact);
  }
  _syncToggleButtons();
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
  _cursor = "";
  _nextCursor = "";
  _cursorStack = [];
  _sendListFiles();
}

export function ensureFilesListLoaded({ sync = false } = {}) {
  if (sync) {
    _sendListFiles();
    return;
  }
  if (!isWorkbenchPanelPreferredVisible("files") || _loadedOnce || _loadRequested) return;
  _sendListFiles();
}

function _loadPage(offset) {
  _offset = offset;
  _cursor = "";
  _nextCursor = "";
  _cursorStack = [];
  _sendListFiles();
}

function _loadNextPage() {
  if (!_nextCursor) return;
  _cursorStack.push(_cursor);
  _offset += _LIMIT;
  _cursor = _nextCursor;
  _sendListFiles();
}

function _loadPrevPage() {
  if (!_cursorStack.length) return;
  _cursor = _cursorStack.pop() || "";
  _offset = Math.max(0, _offset - _LIMIT);
  _sendListFiles();
}

function _sendListFiles() {
  _loadRequested = true;
  const msg = { type: "list_files", offset: _offset, limit: _LIMIT };
  if (_cursor) msg.cursor = _cursor;
  if (_sourceFilter !== "all") msg.source = _sourceFilter;
  if (_query) msg.query = _query;
  if (_minRating) msg.min_rating = _minRating;
  if (_tagFilters.length) msg.tags = [..._tagFilters];
  if (_sortMode !== "recent") msg.sort = _sortMode;
  send(msg);
}

function _resetFilePagingAndRefresh() {
  _offset = 0;
  _cursor = "";
  _nextCursor = "";
  _cursorStack = [];
  _sendListFiles();
}

function _tagKey(value) {
  return String(value || "").trim().toLocaleLowerCase();
}

function _openTagFilterDialog() {
  const selected = new Set(_tagFilters.map(_tagKey));
  const facets = [..._tagFacets];
  for (const tag of _tagFilters) {
    if (!facets.some(item => _tagKey(item.key || item.name) === _tagKey(tag))) {
      facets.push({ name: tag, key: _tagKey(tag), count: 0 });
    }
  }
  const options = facets.length
    ? facets.map(item => {
        const name = String(item.name || item.key || "");
        const key = _tagKey(item.key || name);
        return `<label class="file-tag-filter-option">
          <input type="checkbox" data-file-tag-filter data-tag="${escHtml(name)}" ${selected.has(key) ? "checked" : ""}>
          <span>${escHtml(name)}</span><small>${Number(item.count || 0)}</small>
        </label>`;
      }).join("")
    : '<div class="file-tag-filter-empty">还没有可筛选的标签</div>';
  openDialog({
    title: "筛选文件标签",
    cardClass: "app-modal-card--file-tags",
    html: `<div class="file-tag-filter-dialog">${options}</div>`,
    actions: [
      { label: "取消", secondary: true, onClick: () => closeModal() },
      {
        label: "应用",
        onClick: () => {
          _tagFilters = [...document.querySelectorAll("[data-file-tag-filter]:checked")]
            .map(input => input.dataset.tag || "")
            .filter(Boolean);
          closeModal();
          _resetFilePagingAndRefresh();
        },
      },
    ],
  });
}

function _openFileTagEditor(item) {
  const manual = Array.isArray(item.manual_tags) ? item.manual_tags : [];
  const automatic = (Array.isArray(item.tags) ? item.tags : [])
    .filter(tag => tag?.source === "auto")
    .map(tag => tag.name)
    .filter(Boolean);
  openDialog({
    title: `编辑标签 · ${item.name || "文件"}`,
    cardClass: "app-modal-card--file-tags",
    html: `<div class="file-tag-editor">
      <label for="file-manual-tags-input">手动标签</label>
      <textarea id="file-manual-tags-input" rows="3" maxlength="400"
        placeholder="用逗号分隔，例如：战略，传音，核心资料">${escHtml(manual.join("，"))}</textarea>
      <div class="file-tag-editor-hint">最多 12 个标签；手动标签不会被自动标签覆盖。</div>
      <div class="file-tag-editor-auto">
        <span>自动标签</span>
        ${automatic.length
          ? automatic.map(tag => `<span class="file-tag-chip file-tag-chip--auto">${escHtml(tag)}</span>`).join("")
          : '<small>暂无</small>'}
      </div>
    </div>`,
    actions: [
      { label: "取消", secondary: true, onClick: () => closeModal() },
      {
        label: "保存",
        onClick: () => {
          const value = document.getElementById("file-manual-tags-input")?.value || "";
          const manualTags = value.split(/[,，\n]/).map(tag => tag.trim()).filter(Boolean);
          send({ type: "update_file_metadata", file_id: item.file_id, manual_tags: manualTags });
          closeModal();
        },
      },
    ],
    onOpen: () => document.getElementById("file-manual-tags-input")?.focus(),
  });
}

function _renderFileFilters(list) {
  let filterBar = document.getElementById("files-filter-bar");
  if (!filterBar) {
    filterBar = document.createElement("div");
    filterBar.id = "files-filter-bar";
    filterBar.className = "files-filter-bar";
    list.parentElement?.insertBefore(filterBar, list);
  }
  filterBar.innerHTML = `
    <button id="files-important-filter" class="files-filter-btn${_minRating >= 4 ? " files-filter-btn--active" : ""}"
      type="button" aria-pressed="${_minRating >= 4 ? "true" : "false"}" title="只显示四星及以上文件">★ 重要</button>
    <button id="files-tag-filter" class="files-filter-btn${_tagFilters.length ? " files-filter-btn--active" : ""}"
      type="button" title="按标签筛选"># 标签${_tagFilters.length ? ` · ${_tagFilters.length}` : ""}</button>
    <select id="files-sort" class="files-sort" aria-label="文件排序">
      <option value="recent"${_sortMode === "recent" ? " selected" : ""}>最近更新</option>
      <option value="rating"${_sortMode === "rating" ? " selected" : ""}>重要程度</option>
    </select>
    ${_tagFilters.length ? `<div class="files-active-tags">${_tagFilters.map(tag =>
      `<button type="button" class="files-active-tag" data-tag="${escHtml(tag)}" title="移除筛选">${escHtml(tag)} ×</button>`
    ).join("")}</div>` : ""}
  `;
  filterBar.querySelector("#files-important-filter")?.addEventListener("click", () => {
    _minRating = _minRating >= 4 ? 0 : 4;
    _resetFilePagingAndRefresh();
  });
  filterBar.querySelector("#files-tag-filter")?.addEventListener("click", _openTagFilterDialog);
  filterBar.querySelector("#files-sort")?.addEventListener("change", (ev) => {
    _sortMode = ev.target.value === "rating" ? "rating" : "recent";
    _resetFilePagingAndRefresh();
  });
  filterBar.querySelectorAll(".files-active-tag").forEach(btn => {
    btn.addEventListener("click", () => {
      const key = _tagKey(btn.dataset.tag);
      _tagFilters = _tagFilters.filter(tag => _tagKey(tag) !== key);
      _resetFilePagingAndRefresh();
    });
  });
}

// ── Render ────────────────────────────────────────────────────────────────────

export function renderFiles(data) {
  _loadedOnce = true;
  _loadRequested = false;
  _total = data.total ?? 0;
  _offset = data.offset ?? _offset;
  _nextCursor = data.next_cursor || "";
  _tagFacets = Array.isArray(data.tag_facets) ? data.tag_facets : _tagFacets;

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
  ).join("") + `<button id="files-mark-all-read" class="files-mark-all-read${_unseenGeneratedFiles.size ? "" : " hidden"}"
    type="button" title="Mark every generated file as read" aria-label="Mark all generated files as read">全部已读</button>`;
  tabBar.querySelectorAll(".files-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      _sourceFilter = btn.dataset.source;
      _offset = 0;
      _cursor = "";
      _nextCursor = "";
      _cursorStack = [];
      _sendListFiles();
    });
  });
  tabBar.querySelector("#files-mark-all-read")?.addEventListener("click", () => {
    markGeneratedArtifactsSeen();
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
      _cursor = "";
      _nextCursor = "";
      _cursorStack = [];
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
      _cursor = "";
      _nextCursor = "";
      _cursorStack = [];
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

  _renderFileFilters(list);

  const items = data.items || [];
  _visibleItemsById = new Map(items.map(item => [String(item.file_id || ""), item]));

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
    const rating = Math.max(0, Math.min(5, Number(item.rating || 0)));
    const tags = Array.isArray(item.tags) ? item.tags : [];
    const ratingHtml = `<div class="file-rating file-interactive" role="group" aria-label="${escHtml(item.name)} importance">
      ${[1, 2, 3, 4, 5].map(value => `<button type="button" class="file-rating-star${value <= rating ? " is-filled" : ""}"
        data-file-id="${escHtml(item.file_id || "")}" data-rating="${value}" title="${value} 星" aria-label="设为 ${value} 星" aria-pressed="${value <= rating ? "true" : "false"}">★</button>`).join("")}
      ${rating ? `<span class="file-rating-value">${rating}</span>` : ""}
    </div>`;
    const tagsHtml = tags.length
      ? `<div class="file-tags file-interactive">${tags.slice(0, 2).map(tag =>
          `<button type="button" class="file-tag-chip${tag.source === "auto" ? " file-tag-chip--auto" : ""}"
            data-tag="${escHtml(tag.name || "")}" title="${tag.source === "auto" ? "自动标签" : "手动标签"}：${escHtml(tag.name || "")}">${escHtml(tag.name || "")}</button>`
        ).join("")}${tags.length > 2 ? `<button type="button" class="file-tag-more" data-file-id="${escHtml(item.file_id || "")}">+${tags.length - 2}</button>` : ""}</div>`
      : "";
    return `<div class="file-item${isPreviewable ? " file-item--preview" : " file-item--no-preview"}${isUnseen ? " file-item--new" : ""}"
              data-url="${escHtml(item.url)}"
              data-name="${escHtml(item.name)}"
              data-file-id="${escHtml(item.file_id || "")}"
              data-filename="${escHtml(item.filename)}"
              data-preview-type="${previewType}"
              title="${isPreviewable ? "Click to preview" : item.name}">
      <div class="file-item-ext">${escHtml(ext)}</div>
      <div class="file-item-info">
        <div class="file-item-name">${escHtml(item.name)}${badge}</div>
        <div class="file-item-meta" title="Last updated: ${escHtml(updatedTitle)}">${escHtml(sizeStr)} · Updated ${escHtml(updatedStr)}</div>
        <div class="file-item-taxonomy">${ratingHtml}${tagsHtml}</div>
      </div>
      <div class="file-item-actions">
        <button class="file-item-attach" data-file-id="${escHtml(item.file_id || "")}" title="Attach file">Attach</button>
        <button class="file-item-tags-edit" data-file-id="${escHtml(item.file_id || "")}" title="编辑标签">#</button>
        <button class="file-item-del" data-file-id="${escHtml(item.file_id || "")}" data-filename="${escHtml(item.filename)}" title="Delete file">✕</button>
      </div>
    </div>`;
  }).join("");

  list.querySelectorAll(".file-item--preview").forEach(el => {
    el.addEventListener("click", (ev) => {
      if (ev.target.closest(".file-item-actions, .file-interactive")) return;
      markGeneratedArtifactsSeen({
        file_id: el.dataset.fileId || "",
        url: el.dataset.url || "",
        name: el.dataset.name || "",
      });
      const item = { url: el.dataset.url, name: el.dataset.name };
      _openPreviewByItem(item);
    });
  });

  list.querySelectorAll(".file-rating-star").forEach(btn => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const fileId = String(btn.dataset.fileId || "");
      const item = _visibleItemsById.get(fileId);
      const selected = Number(btn.dataset.rating || 0);
      const nextRating = Number(item?.rating || 0) === selected ? 0 : selected;
      send({ type: "update_file_metadata", file_id: fileId, rating: nextRating });
    });
  });

  list.querySelectorAll(".file-tag-chip").forEach(btn => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const tag = String(btn.dataset.tag || "").trim();
      if (!tag || _tagFilters.some(value => _tagKey(value) === _tagKey(tag))) return;
      _tagFilters = [..._tagFilters, tag];
      _resetFilePagingAndRefresh();
    });
  });

  list.querySelectorAll(".file-tag-more, .file-item-tags-edit").forEach(btn => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const item = _visibleItemsById.get(String(btn.dataset.fileId || ""));
      if (item) _openFileTagEditor(item);
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
    const hasPrev = _cursorStack.length > 0 || _offset > 0;
    const hasNext = !!data.has_more;
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
      document.getElementById("files-pag-prev")?.addEventListener("click", () => (
        _cursorStack.length ? _loadPrevPage() : _loadPage(_offset - _LIMIT)
      ));
      document.getElementById("files-pag-next")?.addEventListener("click", () => (
        _nextCursor ? _loadNextPage() : _loadPage(_offset + _LIMIT)
      ));
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

export function handleFileMetadataUpdated(data) {
  if (!data.ok) {
    showToast(`文件信息更新失败：${data.error || "unknown"}`, "error");
    return;
  }
  refreshFilesList();
}

export function closeWorkbenchPreview() {
  try {
    _workbenchPreviewCleanup?.();
  } finally {
    _workbenchPreviewCleanup = null;
  }
  if (els.workbenchPreviewTitle) els.workbenchPreviewTitle.textContent = "No preview selected";
  if (els.workbenchPreviewMeta) els.workbenchPreviewMeta.textContent = "";
  _workbenchPreviewItem = null;
  _workbenchPreviewPinned = false;
  if (els.workbenchPreviewBody) {
    els.workbenchPreviewBody.className = "workbench-preview-body hidden";
    els.workbenchPreviewBody.innerHTML = "";
  }
  if (els.workbenchPreviewEmpty) els.workbenchPreviewEmpty.classList.remove("hidden");
  if (els.workbenchPreviewPin) els.workbenchPreviewPin.classList.add("hidden");
  if (els.workbenchPreviewClose) els.workbenchPreviewClose.classList.add("hidden");
  if (els.workbenchPreview) els.workbenchPreview.classList.add("hidden");
  _persistWorkbenchPreview();
  refreshWorkbenchVisibility();
}

function _extPreviewKind(name = "") {
  const lower = String(name || "").toLowerCase();
  if (lower.endsWith(".md")) return "Markdown";
  if (lower.endsWith(".html") || lower.endsWith(".htm")) return "HTML";
  if (lower.endsWith(".pdf")) return "PDF";
  if (/\.(jpe?g|png|gif|webp|svg|bmp|ico)$/.test(lower)) return "Image";
  return "File";
}

function _openPreviewByItem(item) {
  const lower = String(item?.name || "").toLowerCase();
  if (lower.endsWith(".md")) return _previewMarkdown(item);
  if (lower.endsWith(".html") || lower.endsWith(".htm")) return _previewHtml(item);
  if (lower.endsWith(".pdf")) return _previewPdf(item);
  if (/\.(jpe?g|png|gif|webp|svg|bmp|ico)$/.test(lower)) return _previewImage(item);
  return null;
}

function _openModalPreview(item, html, { meta = "", bodyClass = "", onOpen = null, onClose = null, download = null } = {}) {
  const summary = [meta, item?.source === "generated" ? "Generated" : ""].filter(Boolean).join(" · ");
  openDialog({
    title: item?.name || "Preview",
    cardClass: "app-modal-card--document",
    html: `
      <div class="file-preview-dialog">
        ${summary ? `<div class="file-preview-dialog-meta">${escHtml(summary)}</div>` : ""}
        <div class="file-preview-dialog-body${bodyClass ? ` ${bodyClass}` : ""}">${html}</div>
      </div>
    `,
    actions: [
      ...(typeof download === "function" ? [{
        label: "Download",
        secondary: true,
        onClick: () => download(),
      }] : []),
      {
        label: "Close",
        secondary: false,
        onClick: () => closeModal(),
      },
    ],
    onOpen,
    onClose,
  });
}

function _syncWorkbenchPreviewHeader() {
  if (els.workbenchPreviewPin) {
    els.workbenchPreviewPin.classList.toggle("active", _workbenchPreviewPinned);
    els.workbenchPreviewPin.setAttribute("aria-pressed", _workbenchPreviewPinned ? "true" : "false");
    els.workbenchPreviewPin.textContent = _workbenchPreviewPinned ? "Pinned" : "Pin";
    els.workbenchPreviewPin.title = _workbenchPreviewPinned ? "Keep this preview as the current session reference" : "Pin this preview for the current session";
  }
  if (els.workbenchPreview) {
    els.workbenchPreview.dataset.pinned = _workbenchPreviewPinned ? "true" : "false";
  }
}

function _persistWorkbenchPreview() {
  const sessionId = getCurrentSessionId();
  if (!_workbenchPreviewItem) {
    setWorkbenchPreviewState(sessionId, null);
    return;
  }
  setWorkbenchPreviewState(sessionId, {
    pinned: _workbenchPreviewPinned,
    item: { ..._workbenchPreviewItem },
  });
}

function _restoreWorkbenchPreviewForSession(sessionId) {
  const snapshot = getWorkbenchPreviewState(sessionId);
  _workbenchPreviewPinned = Boolean(snapshot?.pinned);
  if (!snapshot?.item?.name || !snapshot?.item?.url) {
    closeWorkbenchPreview();
    return;
  }
  // File and artifact previews are modal-first now; do not auto-open
  // previews when switching session context.
  closeWorkbenchPreview();
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
  _openModalPreview(
    item,
    `<div class="file-preview-frame file-preview-html"><iframe src="${escHtml(url)}" sandbox="allow-scripts" referrerpolicy="no-referrer" loading="lazy"></iframe></div>`,
    {
      meta: `${_extPreviewKind(item.name)} preview`,
      download: () => _downloadFile(item, "text/html"),
    },
  );
}

function _previewPdf(item) {
  const apiKey = state.apiKey || "";
  const url = resolveFileUrl(item.url, apiKey);
  _openModalPreview(
    item,
    `<div class="file-preview-frame file-preview-pdf"><iframe src="${escHtml(url)}" loading="lazy"></iframe></div>`,
    {
      meta: `${_extPreviewKind(item.name)} preview`,
      download: () => _downloadFile(item, "application/pdf"),
    },
  );
}

function _previewImage(item) {
  const apiKey = state.apiKey || "";
  const url = resolveFileUrl(item.url, apiKey);
  _openModalPreview(
    item,
    `<div class="file-preview-frame file-preview-image"><img src="${escHtml(url)}" alt="${escHtml(item.name)}"></div>`,
    {
      meta: `${_extPreviewKind(item.name)} preview`,
      download: () => _downloadFile(item),
    },
  );
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
  _openModalPreview(
    item,
    `<div id="${previewId}" class="${markdownSurfaceClass("file")}"></div>`,
    {
      meta: `${_extPreviewKind(item.name)} preview`,
      bodyClass: "file-preview-body",
      onOpen: () => {
        const previewEl = document.getElementById(previewId);
        setMarkdownContent(previewEl, text, { surface: "file", className: "file-preview-body" });
      },
      onClose: () => {
        const previewEl = document.getElementById(previewId);
        unmountMarkdown(previewEl);
      },
      download: () => _downloadFile(item, "text/markdown"),
    },
  );
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
