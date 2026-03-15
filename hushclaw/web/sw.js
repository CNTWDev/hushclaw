const CACHE = "hushclaw-v1";
const STATIC = ["/", "/app.js", "/style.css", "/manifest.json", "/icon.svg"];

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

self.addEventListener("fetch", e => {
  // Only intercept same-origin GET requests; skip WebSocket and API calls
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.host !== location.host) return;
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
