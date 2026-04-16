/**
 * transsion/forum.js — Community forum UI for HushClaw.
 *
 * Self-contained: no imports from core HushClaw modules except
 * renderMarkdown (for post content) and escHtml (for safety).
 *
 * Views:
 *   "list"    — boards filter + post list
 *   "detail"  — single post with comments
 *   "compose" — create / edit a post
 *   "login"   — shown when not authenticated
 */

import { renderMarkdown } from "../modules/markdown.js";
import { escHtml }        from "../modules/state.js";
import { isAuthed, getUser } from "./auth.js";
import { api }            from "./api.js";
import { openConfirm }    from "../modules/modal.js";
import { renderLoadingMarkup } from "../modules/loading.js";

// ── Forum state ─────────────────────────────────────────────────────────────

const f = {
  view:        "list",   // "list" | "detail" | "compose" | "login"
  boards:      [],
  boardId:     0,        // 0 = all
  sort:        "latest",
  keyword:     "",
  posts:       [],
  postPage:    1,
  postTotal:   0,
  loading:     false,
  currentPost: null,     // PostDetail
  comments:    [],
  commentPage: 1,
  commentTotal:0,
  editingPost: null,     // null = new post, PostDetail = editing
  myUserId:    0,
  listDirty:   false,
  listReqSeq:  0,
  detailReqSeq:0,
  postCache:   new Map(), // key => { ts, data }
};

const POST_CACHE_TTL_MS = 30_000;

// ── Panel root ──────────────────────────────────────────────────────────────

function panelEl() { return document.getElementById("panel-forum"); }

function _readStoreKey() {
  const email = (getUser()?.email || "anon").toLowerCase();
  return `hc_forum_read_posts_${email}`;
}

function _loadReadSet() {
  try {
    const raw = localStorage.getItem(_readStoreKey()) || "[]";
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr.map(v => String(v)) : []);
  } catch {
    return new Set();
  }
}

function _saveReadSet(readSet) {
  try {
    const arr = [...readSet];
    const trimmed = arr.length > 2000 ? arr.slice(arr.length - 2000) : arr;
    localStorage.setItem(_readStoreKey(), JSON.stringify(trimmed));
  } catch {
    // ignore
  }
}

function _isPostRead(postId) {
  if (postId == null) return false;
  return _loadReadSet().has(String(postId));
}

function _markPostRead(postId, likes, comments) {
  if (postId == null) return;
  const readSet = _loadReadSet();
  const key = String(postId);
  readSet.add(key);
  _saveReadSet(readSet);
  // Always advance the stat snapshot so the baseline moves to "right now".
  if (likes !== undefined || comments !== undefined) {
    _savePostSnap(postId, likes || 0, comments || 0);
  }
}

// ── Post stats snapshot ──────────────────────────────────────────────────────
// Stores {likes, comments, ts} per post at the time of last read.
// Compared against live API counts to compute "+N" activity deltas on the list.

function _snapStoreKey() {
  const email = (getUser()?.email || "anon").toLowerCase();
  return `hc_forum_snap_${email}`;
}

function _loadSnapMap() {
  try {
    const raw = localStorage.getItem(_snapStoreKey()) || "{}";
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function _saveSnapMap(map) {
  try {
    const entries = Object.entries(map);
    if (entries.length > 2000) {
      entries.sort((a, b) => (b[1].ts || 0) - (a[1].ts || 0));
      localStorage.setItem(
        _snapStoreKey(),
        JSON.stringify(Object.fromEntries(entries.slice(0, 2000))),
      );
    } else {
      localStorage.setItem(_snapStoreKey(), JSON.stringify(map));
    }
  } catch { /* ignore quota/parse errors */ }
}

function _savePostSnap(postId, likes, comments) {
  if (postId == null) return;
  const map = _loadSnapMap();
  map[String(postId)] = { likes: likes || 0, comments: comments || 0, ts: Date.now() };
  _saveSnapMap(map);
}

function _getPostSnap(postId) {
  if (postId == null) return null;
  const map = _loadSnapMap();
  return map[String(postId)] || null;
}

// ── Public API ───────────────────────────────────────────────────────────────

let _initialized = false;

/** Called by index.js when the Forum tab is activated. */
export function onForumActivate() {
  if (!isAuthed()) { renderLoginPrompt(); return; }
  if (!_initialized) { _initialized = true; _bootstrap(); }
  else _renderCurrentView();
}

/** Called by index.js when hc:forum-unauthed fires. */
export function onForumUnauthed() {
  _initialized = false;
  f.view = "login";
  f.postCache.clear();
  f.listDirty = false;
  renderLoginPrompt();
}

/**
 * Open the compose view pre-filled with title and content.
 * Called from chat.js "Share to Forum" button.
 * Requires the forum panel to already be visible (caller should switch tab first).
 */
export function openComposeWith(title, content) {
  if (!isAuthed()) { renderLoginPrompt(); return; }
  // Bootstrap boards list if not yet loaded
  if (!_initialized) {
    _initialized = true;
    // Load boards first, then open compose
    api.listBoards().then(data => {
      f.boards = data.items || data.boards || (Array.isArray(data) ? data : []);
      _openComposePreFilled(title, content);
    }).catch(() => _openComposePreFilled(title, content));
  } else {
    _openComposePreFilled(title, content);
  }
}

function _openComposePreFilled(title, content) {
  f.editingPost = null;
  f.view = "compose";
  f._prefillTitle   = title;
  f._prefillContent = content;
  _renderComposeView();
}

// ── Bootstrap ────────────────────────────────────────────────────────────────

async function _bootstrap() {
  f.view = "list";
  _setLoading(true);
  try {
    const data = await api.listBoards();
    f.boards = data.items || [];
    // Try to get current user id for author-action visibility
    try { const me = await api.getMe(); f.myUserId = me?.user?.userId || 0; } catch { /* ignore */ }
  } catch (err) {
    _renderError("Failed to load boards: " + err.message);
    return;
  } finally { _setLoading(false); }
  await _loadPosts(1);
}

// ── Data loading ─────────────────────────────────────────────────────────────

const PAGE_SIZE = 20;

function _postsCacheKey(boardId, sort, page, keyword = "") {
  const q = String(keyword || "").trim().toLowerCase();
  return `${Number(boardId || 0)}|${sort || "latest"}|${Number(page || 1)}|${q}`;
}

function _getCachedPosts(boardId, sort, page, keyword = "") {
  const key = _postsCacheKey(boardId, sort, page, keyword);
  return f.postCache.get(key) || null;
}

function _isCacheFresh(entry) {
  if (!entry?.ts) return false;
  return (Date.now() - entry.ts) <= POST_CACHE_TTL_MS;
}

function _setCachedPosts(boardId, sort, page, data, keyword = "") {
  const key = _postsCacheKey(boardId, sort, page, keyword);
  f.postCache.set(key, { ts: Date.now(), data });
  // Soft cap to avoid unbounded growth when user flips many pages/filters.
  if (f.postCache.size > 60) {
    const firstKey = f.postCache.keys().next().value;
    if (firstKey) f.postCache.delete(firstKey);
  }
}

function _applyPostsData(data, page) {
  f.posts     = data.items || [];
  f.postPage  = data.paging?.page  || page;
  f.postTotal = data.paging?.total || 0;
}

async function _loadPosts(page = 1, { inline = false, soft = false, force = false } = {}) {
  const boardId = f.boardId;
  const sort = f.sort;
  const keyword = String(f.keyword || "").trim();
  const cached = _getCachedPosts(boardId, sort, page, keyword);
  const shouldUseCache = !force && !f.listDirty && _isCacheFresh(cached);
  if (shouldUseCache) {
    _applyPostsData(cached.data, page);
    _renderListView();
    return;
  }

  if (inline) {
    const listEl = document.getElementById("forum-post-list");
    if (listEl && !soft) {
      listEl.innerHTML = renderLoadingMarkup({ status: "加载中…", compact: true, height: 80 });
    }
  } else {
    if (!soft) _setLoading(true);
  }
  const reqSeq = ++f.listReqSeq;
  try {
    const data = keyword
      ? await api.searchPosts(keyword, boardId, sort, page)
      : await api.listPosts(boardId, sort, page);
    if (reqSeq !== f.listReqSeq) return;
    _setCachedPosts(boardId, sort, page, data, keyword);
    _applyPostsData(data, page);
    f.listDirty = false;
  } catch (err) {
    if (reqSeq !== f.listReqSeq) return;
    _renderError("Failed to load posts: " + err.message);
    return;
  } finally { if (!inline && !soft && reqSeq === f.listReqSeq) _setLoading(false); }
  _renderListView();
}

function _renderDetailSkeleton(postId) {
  const el = panelEl();
  if (!el) return;
  el.innerHTML = `
    <div class="forum-panel forum-detail-panel">
      <div class="forum-detail-nav">
        <button class="forum-back-btn" id="forum-btn-back-skeleton">← 返回</button>
      </div>
      ${renderLoadingMarkup({ status: "加载帖子中…", compact: true, height: 140 })}
    </div>`;
  el.querySelector("#forum-btn-back-skeleton")?.addEventListener("click", () => {
    f.view = "list";
    _renderListView();
    void _loadPosts(f.postPage || 1, { inline: true, soft: true, force: f.listDirty });
  });
}

function _sameId(a, b) {
  if (a == null || b == null) return false;
  return String(a) === String(b);
}

function _updatePostInList(postId, updater) {
  if (!postId || typeof updater !== "function" || !Array.isArray(f.posts)) return;
  const idx = f.posts.findIndex(p => _sameId(p.id, postId));
  if (idx < 0) return;
  const next = updater({ ...f.posts[idx] });
  if (!next) return;
  f.posts[idx] = next;
  // Also patch current page cache for immediate consistency.
  const cache = _getCachedPosts(f.boardId, f.sort, f.postPage);
  if (cache?.data?.items) {
    const cIdx = cache.data.items.findIndex(p => _sameId(p.id, postId));
    if (cIdx >= 0) cache.data.items[cIdx] = { ...next };
  }
}

async function _confirmDelete(entityText) {
  return openConfirm({
    title: "确认删除",
    message: `确认删除${entityText}？此操作不可撤销。`,
    confirmText: "删除",
    cancelText: "取消",
    dangerConfirm: true,
  });
}

async function _loadPost(postId, { soft = true } = {}) {
  const reqSeq = ++f.detailReqSeq;
  _markPostRead(postId);
  if (soft) {
    f.view = "detail";
    _renderDetailSkeleton(postId);
  } else {
    _setLoading(true);
  }
  try {
    const [postData, commentsData] = await Promise.all([
      api.getPost(postId),
      api.listComments(postId, 1),
    ]);
    if (reqSeq !== f.detailReqSeq) return;
    f.currentPost   = postData?.post  || null;
    f.comments      = commentsData?.items  || [];
    f.commentPage   = 1;
    f.commentTotal  = commentsData?.paging?.total || 0;
    f.view          = "detail";
    // Update snapshot with the detail-level counts (fresher and more accurate
    // than the list data saved at click time; moves the baseline forward).
    if (f.currentPost) {
      _savePostSnap(
        f.currentPost.id,
        f.currentPost.likeCount || 0,
        f.commentTotal || 0,
      );
    }
  } catch (err) {
    if (reqSeq !== f.detailReqSeq) return;
    _renderError("Failed to load post: " + err.message);
    return;
  } finally { if (!soft && reqSeq === f.detailReqSeq) _setLoading(false); }
  _renderDetailView();
}

async function _loadMoreComments() {
  const page = f.commentPage + 1;
  try {
    const data = await api.listComments(f.currentPost.id, page);
    f.comments    = [...f.comments, ...(data.items || [])];
    f.commentPage = page;
    _appendComments(data.items || []);
  } catch (err) { _showErr(err.message); }
}

// ── Views ─────────────────────────────────────────────────────────────────────

function _renderCurrentView() {
  if      (f.view === "list")    _renderListView();
  else if (f.view === "detail")  _renderDetailView();
  else if (f.view === "compose") _renderComposeView();
  else                           renderLoginPrompt();
}

function renderLoginPrompt() {
  const el = panelEl();
  if (!el) return;
  el.innerHTML = `
    <div class="forum-login-wrap">
      <div class="forum-login-card">
        <div class="forum-login-icon">💬</div>
        <h2 class="forum-login-title">Community Forum</h2>
        <p class="forum-login-desc">Sign in with your Transsion enterprise account to access the community forum.</p>
        <p class="forum-login-hint">Go to <strong>Settings → Model</strong>, select the <strong>Transsion / TEX AI</strong> provider and complete login.</p>
      </div>
    </div>`;
}

function _renderListView() {
  const el = panelEl();
  if (!el) return;
  el.innerHTML = _buildListHtml();
  _bindListEvents();
}

function _buildListHtml() {
  const keyword = escHtml(f.keyword || "");
  const boardTabs = [
    `<button class="forum-board-tab${f.boardId === 0 ? " active" : ""}" data-board="0">全部</button>`,
    ...f.boards.map(b =>
      `<button class="forum-board-tab${f.boardId === b.id ? " active" : ""}" data-board="${b.id}">${escHtml(b.name)}</button>`
    ),
  ].join("");

  const sortTabs = `
    <button class="forum-sort-tab${f.sort === "latest" ? " active" : ""}" data-sort="latest">最新</button>
    <button class="forum-sort-tab${f.sort === "hot"    ? " active" : ""}" data-sort="hot">热门</button>`;

  const postsHtml = f.posts.length
    ? f.posts.map(_buildPostCard).join("")
    : `<div class="forum-empty">暂无帖子，来发布第一篇吧！</div>`;

  return `
    <div class="forum-panel">
      <div class="forum-toolbar">
        <div class="forum-board-tabs">${boardTabs}</div>
        <div class="forum-toolbar-right">
          <div class="forum-search-wrap">
            <input id="forum-search-input" class="forum-search-input" type="text"
                   maxlength="64" placeholder="搜索标题/摘要…" value="${keyword}">
            <button class="forum-search-btn" id="forum-btn-search" title="搜索">搜索</button>
            ${f.keyword ? `<button class="forum-search-clear-btn" id="forum-btn-search-clear" title="清空搜索">清空</button>` : ""}
          </div>
          <div class="forum-sort-tabs">${sortTabs}</div>
          <button class="forum-refresh-btn" id="forum-btn-refresh" title="刷新">
            <svg width="13" height="13" viewBox="0 0 14 14" fill="none">
              <path d="M12 7A5 5 0 1 1 9 2.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M9 1v2.5H11.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
          <button class="forum-new-btn" id="forum-btn-new" title="发布帖子（+50 积分）">
            ✏️ 发帖 <span class="forum-points-hint">+50</span>
          </button>
        </div>
      </div>
      <div class="forum-list" id="forum-post-list">${postsHtml}</div>
      ${_buildPaginationHtml()}
    </div>`;
}

function _buildPaginationHtml() {
  const total      = f.postTotal;
  const pageSize   = PAGE_SIZE;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const cur        = f.postPage;

  if (totalPages <= 1 && total === 0) return "";

  // Build page-number buttons: always show first, last, current ±2, with "…" gaps
  const pages = new Set([1, totalPages]);
  for (let p = Math.max(1, cur - 2); p <= Math.min(totalPages, cur + 2); p++) pages.add(p);
  const sorted = [...pages].sort((a, b) => a - b);

  let btns = "";
  let prev = 0;
  for (const p of sorted) {
    if (prev && p - prev > 1) btns += `<span class="forum-page-ellipsis">…</span>`;
    const active = p === cur ? " active" : "";
    btns += `<button class="forum-page-btn${active}" data-page="${p}">${p}</button>`;
    prev = p;
  }

  const prevDisabled = cur <= 1        ? " disabled" : "";
  const nextDisabled = cur >= totalPages ? " disabled" : "";

  return `
    <div class="forum-pagination">
      <span class="forum-page-info">共 ${total} 篇 · 第 ${cur}/${totalPages} 页</span>
      <div class="forum-page-btns">
        <button class="forum-page-nav" data-page="${cur - 1}"${prevDisabled}>‹ 上一页</button>
        ${btns}
        <button class="forum-page-nav" data-page="${cur + 1}"${nextDisabled}>下一页 ›</button>
      </div>
    </div>`;
}

function _buildPostCard(post) {
  const board   = escHtml(post.board?.name   || "");
  const author  = escHtml(post.author?.displayName || post.author?.username || "");
  const title   = escHtml(post.title || "");
  const time    = _relTime(post.createdAt);
  const pinned  = post.isPinned ? `<span class="forum-pin">置顶</span>` : "";
  const isRead  = _isPostRead(post.id);
  const readCls = isRead ? " read" : " unread";
  const readText = isRead ? "已读" : "未读";

  // Compute activity deltas vs. the snapshot saved when we last opened this post.
  const snap       = isRead ? _getPostSnap(post.id) : null;
  const newLikes    = snap ? Math.max(0, (post.likeCount    || 0) - snap.likes)    : 0;
  const newComments = snap ? Math.max(0, (post.commentCount || 0) - snap.comments) : 0;

  const _statHtml = (count, delta, label) => `
    <span class="forum-stat">
      <span class="forum-stat-num">${count}</span>
      <span class="forum-stat-label">${label}</span>
      ${delta > 0 ? `<span class="forum-stat-delta" title="上次阅读后新增 ${delta}">+${delta}</span>` : ""}
    </span>`;

  const metaHtml = `
    <span class="forum-post-author">${author || "匿名"}</span>
    <span class="forum-meta-sep">·</span>
    <span class="forum-post-time">${time}</span>`;

  return `
    <div class="forum-post-card${readCls}" data-post-id="${post.id}">
      <div class="forum-post-status">
        <span class="forum-unread-dot" aria-hidden="true"></span>
        <span class="forum-read-pill">${readText}</span>
      </div>
      <div class="forum-post-topic">
        <div class="forum-post-line1">
          ${pinned}
          ${board ? `<span class="forum-board-badge">${board}</span>` : ""}
          <span class="forum-post-title">${title}</span>
        </div>
        <div class="forum-post-meta forum-post-meta-mobile">${metaHtml}</div>
      </div>
      <div class="forum-post-side">
        <div class="forum-post-meta forum-post-meta-desktop">${metaHtml}</div>
        <div class="forum-post-stats">
          ${_statHtml(post.viewCount || 0, 0, "浏览")}
          ${_statHtml(post.likeCount || 0, newLikes, "点赞")}
          ${_statHtml(post.commentCount || 0, newComments, "回复")}
        </div>
      </div>
    </div>`;
}

function _bindListEvents() {
  const el = panelEl();
  if (!el) return;

  el.querySelectorAll(".forum-board-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      el.querySelectorAll(".forum-board-tab").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      f.boardId = Number(btn.dataset.board);
      _loadPosts(1, { inline: true, force: Boolean((f.keyword || "").trim()) });
    });
  });
  el.querySelectorAll(".forum-sort-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      el.querySelectorAll(".forum-sort-tab").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      f.sort = btn.dataset.sort;
      _loadPosts(1, { inline: true, force: Boolean((f.keyword || "").trim()) });
    });
  });
  el.querySelectorAll(".forum-post-card").forEach(card => {
    card.addEventListener("click", () => {
      const postId = card.dataset.postId;
      // Pass current counts so the snapshot advances to "just now".
      const post = f.posts.find(p => String(p.id) === String(postId));
      _markPostRead(postId, post?.likeCount, post?.commentCount);
      card.classList.remove("unread");
      card.classList.add("read");
      // Clear "+N" deltas immediately — the user is about to read the post.
      card.querySelectorAll(".forum-stat-delta").forEach(b => b.remove());
      _loadPost(postId);
    });
  });

  // Pagination — delegate to a single listener on the pagination bar
  el.querySelector(".forum-pagination")?.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-page]");
    if (!btn || btn.disabled || btn.hasAttribute("disabled")) return;
    const page = Number(btn.dataset.page);
    if (!page || page === f.postPage) return;
    _loadPosts(page, { inline: true });
    // Scroll panel back to top after page change
    panelEl()?.scrollTo({ top: 0, behavior: "smooth" });
  });

  el.querySelector("#forum-btn-refresh")?.addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    btn.classList.add("spinning");
    btn.disabled = true;
    try {
      // Reload boards and reset to page 1
      const data = await api.listBoards();
      f.boards = data.items || [];
      if (!f.keyword) {
        f.boardId = 0;
        f.sort    = "latest";
      }
      await _loadPosts(1, { inline: true, force: true });
    } catch { /* already shown in _loadPosts */ } finally {
      btn.classList.remove("spinning");
      btn.disabled = false;
    }
  });
  const _triggerSearch = () => {
    const input = el.querySelector("#forum-search-input");
    const q = (input?.value || "").trim();
    if (q.length > 64) { _showErr("搜索关键词最多 64 字"); return; }
    if (q === (f.keyword || "")) return;
    f.keyword = q;
    _loadPosts(1, { inline: true, force: true });
  };
  el.querySelector("#forum-btn-search")?.addEventListener("click", _triggerSearch);
  el.querySelector("#forum-search-input")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") _triggerSearch();
  });
  el.querySelector("#forum-btn-search-clear")?.addEventListener("click", () => {
    f.keyword = "";
    _loadPosts(1, { inline: true, force: true });
  });
  el.querySelector("#forum-btn-new")?.addEventListener("click", () => {
    f.editingPost = null;
    f.view        = "compose";
    _renderComposeView();
  });
}

// ── Detail view ───────────────────────────────────────────────────────────────

function _renderDetailView() {
  const el = panelEl();
  if (!el || !f.currentPost) return;
  el.innerHTML = _buildDetailHtml();
  _bindDetailEvents();
}

function _buildDetailHtml() {
  const p       = f.currentPost;
  const isOwner = f.myUserId && p.author?.userId === f.myUserId;
  const board   = escHtml(p.board?.name || "");
  const author  = escHtml(p.author?.displayName || p.author?.username || "");
  const time    = _fmtDate(p.createdAt);
  const updTime = p.updatedAt && p.updatedAt !== p.createdAt ? ` · 更新于 ${_relTime(p.updatedAt)}` : "";
  const likedCls    = p.isLiked     ? " active" : "";
  const favCls      = p.isFavorited ? " active" : "";

  const ownerBtns = isOwner ? `
    <button class="forum-action-btn secondary" id="forum-btn-edit">编辑</button>
    <button class="forum-action-btn secondary forum-delete-btn" id="forum-btn-delete">删除</button>` : "";

  const contentHtml = renderMarkdown(p.content || "");

  const commentsHtml = f.comments.map(_buildCommentRow).join("");
  const hasMoreCmt   = f.comments.length < f.commentTotal;

  return `
    <div class="forum-panel forum-detail-panel">
      <div class="forum-detail-nav">
        <button class="forum-back-btn" id="forum-btn-back">← 返回</button>
        <div class="forum-detail-actions">${ownerBtns}</div>
      </div>

      <h1 class="forum-detail-title">${escHtml(p.title || "")}</h1>

      <div class="forum-detail-meta">
        ${board ? `<span class="forum-board-badge">${board}</span>` : ""}
        <span class="forum-detail-author">${author}</span>
        <span class="forum-meta-sep">·</span>
        <span class="forum-detail-time">${time}${updTime}</span>
        <span class="forum-meta-sep">·</span>
        <span>👁 ${p.viewCount || 0}</span>
      </div>

      <div class="forum-detail-body bubble markdown-body">${contentHtml}</div>

      <div class="forum-interaction-bar">
        <button class="forum-interact-btn${likedCls}" id="forum-btn-like" data-post-id="${p.id}">
          ♥ <span id="forum-like-count">${p.likeCount || 0}</span>
        </button>
        <button class="forum-interact-btn${favCls}" id="forum-btn-fav" data-post-id="${p.id}">
          ★ <span id="forum-fav-count">${p.favoriteCount || 0}</span>
        </button>
      </div>

      <div class="forum-comments-section">
        <h3 class="forum-comments-heading">💬 评论 <span id="forum-comment-count">(${f.commentTotal})</span></h3>
        <div id="forum-comments-list">${commentsHtml}</div>
        ${hasMoreCmt ? `<div class="forum-load-more"><button class="secondary" id="forum-more-cmt">加载更多评论</button></div>` : ""}

        <div class="forum-comment-form">
          <textarea id="forum-cmt-input" class="forum-cmt-textarea" rows="3"
                    placeholder="写下你的评论…（最多 2000 字）"></textarea>
          <div class="forum-cmt-actions">
            <span class="forum-points-hint-inline">+1 积分</span>
            <button class="forum-submit-btn" id="forum-cmt-submit">发表评论</button>
          </div>
        </div>
      </div>
    </div>`;
}

function _buildCommentRow(cmt) {
  const author    = escHtml(cmt.author?.displayName || cmt.author?.username || "");
  const isOwner   = f.myUserId && (cmt.author?.userId === f.myUserId || f.currentPost?.author?.userId === f.myUserId);
  const deletBtn  = isOwner ? `<button class="forum-cmt-del" data-cmt-id="${cmt.id}">删除</button>` : "";
  return `
    <div class="forum-comment-row" data-cmt-id="${cmt.id}">
      <div class="forum-cmt-avatar">${_initials(author)}</div>
      <div class="forum-cmt-body">
        <div class="forum-cmt-header">
          <span class="forum-cmt-author">${author}</span>
          <span class="forum-cmt-time">${_relTime(cmt.createdAt)}</span>
          ${deletBtn}
        </div>
        <div class="forum-cmt-content">${escHtml(cmt.content || "")}</div>
      </div>
    </div>`;
}

function _appendComments(items) {
  const list = document.getElementById("forum-comments-list");
  if (!list) return;
  items.forEach(cmt => { list.insertAdjacentHTML("beforeend", _buildCommentRow(cmt)); });
  _bindCommentDeleteEvents();
}

function _bindDetailEvents() {
  const el = panelEl();
  if (!el) return;

  el.querySelector("#forum-btn-back")?.addEventListener("click", () => {
    f.view = "list";
    _renderListView();
    void _loadPosts(f.postPage || 1, { inline: true, soft: true, force: f.listDirty });
  });
  el.querySelector("#forum-btn-edit")?.addEventListener("click", () => {
    f.editingPost = f.currentPost;
    f.view        = "compose";
    _renderComposeView();
  });
  el.querySelector("#forum-btn-delete")?.addEventListener("click", async () => {
    const confirmed = await _confirmDelete("这篇帖子");
    if (!confirmed) return;
    try {
      await api.deletePost(f.currentPost.id);
      f.listDirty = true;
      f.view = "list";
      await _loadPosts(1, { inline: true, force: true });
    } catch (err) { _showErr(err.message); }
  });

  // Like / Favorite toggles
  el.querySelector("#forum-btn-like")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    try {
      const res = await api.toggleLike(f.currentPost.id);
      f.currentPost.isLiked    = res.liked;
      f.currentPost.likeCount  = res.likeCount;
      _updatePostInList(f.currentPost.id, (p) => ({ ...p, likeCount: res.likeCount }));
      btn.classList.toggle("active", res.liked);
      const cnt = document.getElementById("forum-like-count");
      if (cnt) cnt.textContent = res.likeCount;
    } catch (err) { _showErr(err.message); }
  });
  el.querySelector("#forum-btn-fav")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    try {
      const res = await api.toggleFavorite(f.currentPost.id);
      f.currentPost.isFavorited    = res.favorited;
      f.currentPost.favoriteCount  = res.favoriteCount;
      btn.classList.toggle("active", res.favorited);
      const cnt = document.getElementById("forum-fav-count");
      if (cnt) cnt.textContent = res.favoriteCount;
    } catch (err) { _showErr(err.message); }
  });

  // Load more comments
  el.querySelector("#forum-more-cmt")?.addEventListener("click", _loadMoreComments);

  // Submit comment
  el.querySelector("#forum-cmt-submit")?.addEventListener("click", async () => {
    const ta = document.getElementById("forum-cmt-input");
    const content = (ta?.value || "").trim();
    if (!content) { _showErr("评论内容不能为空"); return; }
    if (content.length > 2000) { _showErr("评论不能超过 2000 字"); return; }
    const btn = el.querySelector("#forum-cmt-submit");
    btn.disabled = true; btn.textContent = "发表中…";
    try {
      const res = await api.createComment(f.currentPost.id, content);
      const newCmt = res?.comment || { id: Date.now(), content, author: { displayName: getUser()?.displayName || "" }, createdAt: new Date().toISOString() };
      f.comments.push(newCmt);
      f.commentTotal++;
      if (f.currentPost) f.currentPost.commentCount = (f.currentPost.commentCount || 0) + 1;
      _updatePostInList(f.currentPost?.id, (p) => ({ ...p, commentCount: (p.commentCount || 0) + 1 }));
      f.listDirty = true;
      const list = document.getElementById("forum-comments-list");
      if (list) list.insertAdjacentHTML("beforeend", _buildCommentRow(newCmt));
      const cnt = document.getElementById("forum-comment-count");
      if (cnt) cnt.textContent = `(${f.commentTotal})`;
      ta.value = "";
    } catch (err) { _showErr(err.message); }
    finally { btn.disabled = false; btn.textContent = "发表评论"; }
  });

  _bindCommentDeleteEvents();
}

function _bindCommentDeleteEvents() {
  panelEl()?.querySelectorAll(".forum-cmt-del").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const cmtId = btn.dataset.cmtId;
      if (!cmtId) return;
      const confirmed = await _confirmDelete("这条评论");
      if (!confirmed) return;
      try {
        await api.deleteComment(cmtId);
        btn.closest(".forum-comment-row")?.remove();
        f.comments = f.comments.filter(c => !_sameId(c.id, cmtId));
        f.commentTotal = Math.max(0, f.commentTotal - 1);
        if (f.currentPost) f.currentPost.commentCount = Math.max(0, (f.currentPost.commentCount || 0) - 1);
        _updatePostInList(f.currentPost?.id, (p) => ({ ...p, commentCount: Math.max(0, (p.commentCount || 0) - 1) }));
        f.listDirty = true;
        const cnt = document.getElementById("forum-comment-count");
        if (cnt) cnt.textContent = `(${f.commentTotal})`;
      } catch (err) { _showErr(err.message); }
    });
  });
}

// ── Compose view ──────────────────────────────────────────────────────────────

function _renderComposeView() {
  const el = panelEl();
  if (!el) return;
  const isEdit   = Boolean(f.editingPost);
  const prefillT = f._prefillTitle   || "";
  const prefillC = f._prefillContent || "";
  // Consume pre-fill values once
  f._prefillTitle   = "";
  f._prefillContent = "";
  const title    = isEdit ? escHtml(f.editingPost.title   || "") : escHtml(prefillT);
  const content  = isEdit ?         f.editingPost.content || ""  : prefillC;
  const boardOpts = f.boards.map(b => {
    const sel = isEdit && f.editingPost.board?.id === b.id ? " selected" : "";
    return `<option value="${b.id}"${sel}>${escHtml(b.name)}</option>`;
  }).join("");
  const firstBoardId = f.boards[0]?.id || 0;

  el.innerHTML = `
    <div class="forum-panel forum-compose-panel">
      <div class="forum-detail-nav">
        <button class="forum-back-btn" id="compose-btn-back">← 取消</button>
        <h2 class="forum-compose-heading">${isEdit ? "编辑帖子" : "发布新帖子"}</h2>
      </div>

      <div class="forum-compose-form">
        <div class="forum-field">
          <label>版面</label>
          <select id="compose-board" class="forum-select">
            ${f.boards.length ? boardOpts : `<option value="${firstBoardId}">默认</option>`}
          </select>
        </div>
        <div class="forum-field">
          <label>标题 <span class="forum-req">*</span></label>
          <input id="compose-title" type="text" class="forum-input"
                 maxlength="256" placeholder="标题（最多 256 字）" value="${title}">
        </div>
        <div class="forum-field">
          <label>内容 <span class="forum-req">*</span> <span class="forum-field-hint">支持 Markdown</span></label>
          <textarea id="compose-content" class="forum-compose-textarea"
                    rows="12" placeholder="在这里写下你想分享的内容…">${escHtml(content)}</textarea>
        </div>
        <div id="compose-err" class="forum-compose-err" style="display:none"></div>
        <div class="forum-compose-actions">
          <button class="forum-back-btn" id="compose-btn-cancel">取消</button>
          <button class="forum-submit-btn" id="compose-btn-submit">
            ${isEdit ? "保存修改" : "发布帖子 <span class='forum-points-hint'>+50</span>"}
          </button>
        </div>
      </div>
    </div>`;

  const back = (e) => { e.preventDefault(); f.view = f.editingPost ? "detail" : "list"; _renderCurrentView(); };
  el.querySelector("#compose-btn-back")?.addEventListener("click",   back);
  el.querySelector("#compose-btn-cancel")?.addEventListener("click", back);
  el.querySelector("#compose-btn-submit")?.addEventListener("click", _submitPost);
}

async function _submitPost() {
  const titleEl   = document.getElementById("compose-title");
  const contentEl = document.getElementById("compose-content");
  const boardEl   = document.getElementById("compose-board");
  const errEl     = document.getElementById("compose-err");

  const title    = (titleEl?.value   || "").trim();
  const content  = (contentEl?.value || "").trim();
  const boardId  = Number(boardEl?.value || 0);

  if (!title)   { _composeErr("标题不能为空"); return; }
  if (!content) { _composeErr("内容不能为空"); return; }
  if (!boardId) { _composeErr("请选择版面");   return; }
  if (errEl) errEl.style.display = "none";

  const btn = document.getElementById("compose-btn-submit");
  if (btn) { btn.disabled = true; btn.textContent = "提交中…"; }

  try {
    if (f.editingPost) {
      await api.updatePost(f.editingPost.id, { boardId, title, content });
      f.listDirty = true;
      // Reload detail
      await _loadPost(f.editingPost.id);
    } else {
      const res = await api.createPost(boardId, title, content);
      const newId = res?.post?.id || res?.postId;
      f.listDirty = true;
      f.editingPost = null;
      if (newId) { await _loadPost(newId); }
      else { f.view = "list"; await _loadPosts(1, { inline: true, force: true }); }
    }
  } catch (err) {
    _composeErr(err.message);
    if (btn) { btn.disabled = false; btn.textContent = f.editingPost ? "保存修改" : "发布帖子"; }
  }
}

function _composeErr(msg) {
  const el = document.getElementById("compose-err");
  if (!el) return;
  el.textContent = msg;
  el.style.display = "block";
}

// ── Utility helpers ───────────────────────────────────────────────────────────

function _setLoading(v) {
  f.loading = v;
  const el = panelEl();
  if (!el) return;
  if (v) {
    el.innerHTML = renderLoadingMarkup({ status: "加载中…", hint: "正在获取论坛数据…" });
  }
}

function _renderError(msg) {
  const el = panelEl();
  if (el) el.innerHTML = `<div class="forum-error">⚠ ${escHtml(msg)}</div>`;
}

function _showErr(msg) {
  // Lightweight inline error — use a toast or inline div in the current view
  const existing = panelEl()?.querySelector(".forum-inline-err");
  if (existing) { existing.textContent = msg; return; }
  const div = document.createElement("div");
  div.className = "forum-inline-err";
  div.textContent = msg;
  panelEl()?.prepend(div);
  setTimeout(() => div.remove(), 4000);
}

function _relTime(iso) {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)   return "刚刚";
  if (m < 60)  return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24)  return `${h} 小时前`;
  const d = Math.floor(h / 24);
  if (d < 30)  return `${d} 天前`;
  return _fmtDate(iso);
}

function _fmtDate(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
  catch { return iso; }
}

function _initials(name) {
  return (name || "?").slice(0, 2).toUpperCase();
}
