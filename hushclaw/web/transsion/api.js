/**
 * transsion/api.js — Community API calls routed via WebSocket.
 *
 * websockets 16 only accepts GET (WebSocket upgrade) connections.
 * POST requests are rejected before process_request is even called.
 * All forum API calls are therefore sent as WS messages of type
 * "community_proxy" and the response arrives as "community_proxy_result".
 *
 * The browser must have an open WS connection (state.ws) for API calls
 * to work. If the connection is closed the caller will get a rejection.
 */

import { getToken, clearToken } from "./auth.js";
import { state } from "../modules/state.js";

// ── Request counter for unique IDs ──────────────────────────────────────────

let _seq = 0;
function _makeRequestId() {
  return `fp_${Date.now()}_${(++_seq).toString(36)}`;
}

// ── Pending requests map: requestId → { resolve, reject, timer } ────────────

const _pending = new Map();

/**
 * Called by websocket.js when a "community_proxy_result" message arrives.
 * Exported so websocket.js can import and call it.
 */
export function handleCommunityProxyResult(data) {
  const entry = _pending.get(data.request_id);
  if (!entry) return;
  clearTimeout(entry.timer);
  _pending.delete(data.request_id);
  if (data.ok) {
    entry.resolve(data.payload || {});
  } else {
    const code = data.status || 0;
    if (code === 401) {
      clearToken();
      document.dispatchEvent(new CustomEvent("hc:forum-unauthed"));
    }
    entry.reject(Object.assign(new Error(data.error || `Server error (status ${code})`), { code }));
  }
}

// ── Core WS request function ─────────────────────────────────────────────────

function _wsPost(path, payload = {}, auth = true) {
  return new Promise((resolve, reject) => {
    const ws = state.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      reject(new Error("WebSocket not connected — please wait for the server connection"));
      return;
    }

    const token = auth ? getToken() : "";
    if (auth && !token) {
      reject(Object.assign(new Error("Not authenticated"), { code: 401 }));
      return;
    }

    const requestId = _makeRequestId();
    const timer = setTimeout(() => {
      _pending.delete(requestId);
      reject(new Error("Request timed out after 30 s"));
    }, 30_000);

    _pending.set(requestId, { resolve, reject, timer });

    ws.send(JSON.stringify({
      type:       "community_proxy",
      path,
      payload,
      token,
      request_id: requestId,
    }));
  });
}

// ── Public API ────────────────────────────────────────────────────────────────

export const forumApi = (path, payload) => _wsPost(path, payload, true);

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
