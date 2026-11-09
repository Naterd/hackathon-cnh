// Rendered per deploy: the version in the cache name changes this file's bytes, so the
// browser installs the new worker and the old cache gets dropped on activate.
const CACHE = "slidekick-{{ version }}";
const SHELL = ["/", "/static/style.css?v={{ version }}", "/static/app.js?v={{ version }}"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

// Network first, so a deploy shows up on the next load. The cached shell only covers being offline.
// API calls pass straight through: a stale lookup result is worse than an error.
self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;

  event.respondWith(
    fetch(request).catch(async () =>
      (await caches.match(request)) ??
      (request.mode === "navigate" ? caches.match("/") : Response.error()),
    ),
  );
});
