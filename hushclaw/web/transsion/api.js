/**
 * transsion/api.js — HTTP request layer for the HushClaw community API.
 *
 * Two base URLs:
 *   AUTH_BASE  — email-code auth endpoints (no Authorization header)
 *   FORUM_BASE — all community endpoints   (requires pf-sso token)
 *
 * On 401: clears the stored token and dispatches "hc:forum-unauthed"
 *         so forum.js can switch to the login-prompt view.
 */

import { getToken, clearToken } from "./auth.js";

const AUTH_BASE  = "https://bus-ie.aibotplatform.com/assistant/vendor-api/v1/auth";
// Community API is proxied through the HushClaw server to avoid CORS issues
// (browser on http://127.0.0.1:8765 cannot directly fetch https://bus-ie.aibotplatform.com).
const FORUM_BASE = "/proxy/community";

let _reqCounter = 0;

function _makeMeta() {
  return {
    appID:     "hushclaw",
    requestID: `req_${Date.now()}_${(++_reqCounter).toString(36)}`,
    timestamp:  new Date().toISOString(),
  };
}

async function _post(baseUrl, path, payload = {}, auth = true) {
  const headers = { "Content-Type": "application/json" };
  if (auth) {
    const token = getToken();
    if (!token) throw Object.assign(new Error("Not authenticated"), { code: 401 });
    headers["Authorization"] = `pf-sso ${token}`;
  }

  let res;
  try {
    res = await fetch(baseUrl + path, {
      method:  "POST",
      headers,
      body:    JSON.stringify({ metadata: _makeMeta(), payload }),
    });
  } catch (err) {
    throw new Error(`Network error: ${err.message}`);
  }

  let json;
  try { json = await res.json(); }
  catch { throw new Error(`Server returned non-JSON response (HTTP ${res.status})`); }

  const code = json?.metadata?.code ?? -1;
  if (code === 401 || res.status === 401) {
    clearToken();
    document.dispatchEvent(new CustomEvent("hc:forum-unauthed"));
    throw Object.assign(new Error("Session expired — please log in again"), { code: 401 });
  }
  if (code !== 0 && code !== 200) {
    throw new Error(json?.metadata?.debugMessage || `Server error (code ${code})`);
  }
  return json.payload || {};
}

// ── Auth endpoints (no SSO token required) ──────────────────────────────────

export const authApi = (path, payload) => _post(AUTH_BASE, path, payload, false);

// ── Community endpoints (all require pf-sso token) ──────────────────────────

export const forumApi = (path, payload) => _post(FORUM_BASE, path, payload, true);

// ── Convenience wrappers ────────────────────────────────────────────────────

export const api = {
  // Boards
  listBoards: ()                       => forumApi("/board/list", {}),

  // Posts
  listPosts:  (boardId, sort, page)    => forumApi("/post/list", {
    boardId: boardId || 0,
    sort:    sort    || "latest",
    paging:  { page: page || 1, pageSize: 20 },
  }),
  getPost:    (postId)                 => forumApi("/post/detail", { postId }),
  createPost: (boardId, title, content)=> forumApi("/post/create", { boardId, title, content }),
  updatePost: (postId, fields)         => forumApi("/post/update", { postId, ...fields }),
  deletePost: (postId)                 => forumApi("/post/delete", { postId }),
  myPosts:    (page)                   => forumApi("/post/user-list", {
    paging: { page: page || 1, pageSize: 20 },
  }),

  // Comments
  listComments:  (postId, page)        => forumApi("/comment/list", {
    postId,
    paging: { page: page || 1, pageSize: 20 },
  }),
  createComment: (postId, content)     => forumApi("/comment/create", { postId, content }),
  deleteComment: (commentId)           => forumApi("/comment/delete", { commentId }),

  // Interactions
  toggleLike:     (postId)             => forumApi("/interaction/toggle-like",     { postId }),
  toggleFavorite: (postId)             => forumApi("/interaction/toggle-favorite", { postId }),

  // User
  getMe:          ()                   => forumApi("/user/me", {}),
};
