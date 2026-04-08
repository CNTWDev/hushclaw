/**
 * transsion/api.js — Community & Auth API calls via plain HTTP POST.
 *
 * All requests go to the HushClaw HTTP API server (port + 1).
 * The API server proxies to bus-ie.aibotplatform.com, avoiding CORS issues.
 */

import { getToken, clearToken } from "./auth.js";

// Compute the HTTP API base URL from the current page's location.
// WS server runs on location.port (default 8765); HTTP API on port + 1.
function _apiBase() {
  const wsPort = Number(location.port || 8765);
  return `${location.protocol}//${location.hostname}:${wsPort + 1}`;
}

// ── Core HTTP POST helper ─────────────────────────────────────────────────────

async function _post(path, body = {}, token = "") {
  const base = _apiBase();
  const headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = token;

  let resp;
  try {
    resp = await fetch(base + path, {
      method:  "POST",
      headers,
      body:    JSON.stringify(body),
    });
  } catch (err) {
    throw new Error(`Network error: ${err.message}`);
  }

  const text = await resp.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(`Invalid JSON from server (status ${resp.status})`);
  }

  if (!resp.ok) {
    const msg = data?.error || data?.metadata?.debugMessage || `HTTP ${resp.status}`;
    throw Object.assign(new Error(msg), { code: resp.status });
  }

  // Community API responses wrap payload in { metadata, payload }
  if ("payload" in data && "metadata" in data) {
    const code = data.metadata?.code ?? 0;
    if (code !== 0 && code !== 200) {
      const msg = data.metadata?.debugMessage || `API error code ${code}`;
      const err = Object.assign(new Error(msg), { code });
      if (resp.status === 401 || code === 401) {
        clearToken();
        document.dispatchEvent(new CustomEvent("hc:forum-unauthed"));
      }
      throw err;
    }
    return data.payload ?? {};
  }

  return data;
}

// ── Community forum API ───────────────────────────────────────────────────────

function _forumPost(apiPath, payload = {}) {
  const token = getToken();
  if (!token) {
    return Promise.reject(Object.assign(new Error("Not authenticated"), { code: 401 }));
  }
  return _post(`/api/community${apiPath}`, payload, `pf-sso ${token}`);
}

export const api = {
  // Boards
  listBoards: ()                       => _forumPost("/board/list", {}),

  // Posts
  listPosts:  (boardId, sort, page)    => _forumPost("/post/list", {
    boardId: boardId || 0,
    sort:    sort    || "latest",
    paging:  { page: page || 1, pageSize: 20 },
  }),
  getPost:    (postId)                 => _forumPost("/post/detail", { postId }),
  createPost: (boardId, title, content)=> _forumPost("/post/create", { boardId, title, content }),
  updatePost: (postId, fields)         => _forumPost("/post/update", { postId, ...fields }),
  deletePost: (postId)                 => _forumPost("/post/delete", { postId }),
  myPosts:    (page)                   => _forumPost("/post/user-list", {
    paging: { page: page || 1, pageSize: 20 },
  }),

  // Comments
  listComments:  (postId, page)        => _forumPost("/comment/list", {
    postId,
    paging: { page: page || 1, pageSize: 20 },
  }),
  createComment: (postId, content)     => _forumPost("/comment/create", { postId, content }),
  deleteComment: (commentId)           => _forumPost("/comment/delete", { commentId }),

  // Interactions
  toggleLike:     (postId)             => _forumPost("/interaction/toggle-like",     { postId }),
  toggleFavorite: (postId)             => _forumPost("/interaction/toggle-favorite", { postId }),

  // User
  getMe: () => _forumPost("/user/me", {}),
};

// ── Auth API (for future standalone forum login) ──────────────────────────────

export const authApi = {
  sendEmailCode: (email) => _post("/api/auth/send-email-code", { email }),
  login:         (email, code) => _post("/api/auth/email-code-login", { email, code }),
};
