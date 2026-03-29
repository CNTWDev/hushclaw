const CACHE = "hushclaw-v3";
const STATIC = [
  "/",
  "/index.html",
  "/app.js",
  "/style.css",
  "/styles/theme-modes.css",
  "/styles/chat-theme.css",
  "/styles/markdown-tight.css",
  "/styles/ui-theme-unified.css",
  "/manifest.json",
  "/icon.svg",
  // ES modules — must be pre-cached so the app works if the server
  // becomes temporarily unreachable after the first visit.
  "/modules/state.js",
  "/modules/markdown.js",
  "/modules/modal.js",
  "/modules/chat.js",
  "/modules/settings.js",
  "/modules/panels.js",
  "/modules/tasks.js",
  "/modules/theme.js",
  "/modules/updates.js",
  "/modules/websocket.js",
  "/modules/events.js",
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener("message", e => {
  if (e.data && e.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

async function networkFirst(req) {
  try {
    const res = await fetch(req, { cache: "no-store" });
    if (res && res.ok) {
      const c = await caches.open(CACHE);
      c.put(req, res.clone());
    }
    return res;
  } catch (_e) {
    const cached = await caches.match(req);
    if (cached) return cached;
    throw new Error("network and cache missed");
  }
}

self.addEventListener("fetch", e => {
  // Only intercept same-origin GET requests; skip WebSocket and API calls
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.host !== location.host) return;
  if (url.pathname.startsWith("/files/") || url.pathname.startsWith("/upload")) return;
  e.respondWith(
    (async () => {
      try {
        return await networkFirst(e.request);
      } catch (_e) {
        if (e.request.mode === "navigate") {
          return (await caches.match("/index.html")) || (await caches.match("/"));
        }
        return new Response("Offline", { status: 503, statusText: "Offline" });
      }
    })()
  );
});
