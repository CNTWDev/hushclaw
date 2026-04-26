/**
 * http.js — helpers for browser-side access to the companion HTTP API server.
   *
 * The Web UI runs on the WebSocket/static server port, while file downloads and
 * HTTP APIs are served on port + 1. These helpers normalize relative /files/
 * URLs so previews and download links hit the correct origin.
 */

function _apiBase() {
  const wsPort = Number(location.port || 8765);
  return `${location.protocol}//${location.hostname}:${wsPort + 1}`;
}

export function resolveHttpUrl(url) {
  const raw = String(url || "").trim();
  if (!raw) return raw;
  if (raw.startsWith("/files/") || raw.startsWith("/upload") || raw.startsWith("/api/")) {
    return `${_apiBase()}${raw}`;
  }
  return raw;
}

export function withApiKey(url, apiKey = "") {
  const raw = String(url || "").trim();
  if (!raw) return raw;
  const key = String(apiKey || "").trim();
  if (!key) return raw;
  try {
    const u = new URL(raw, location.origin);
    u.searchParams.set("api_key", key);
    return u.toString();
  } catch (_e) {
    return raw.includes("?")
      ? `${raw}&api_key=${encodeURIComponent(key)}`
      : `${raw}?api_key=${encodeURIComponent(key)}`;
  }
}

export function resolveFileUrl(url, apiKey = "") {
  return withApiKey(resolveHttpUrl(url), apiKey);
}
