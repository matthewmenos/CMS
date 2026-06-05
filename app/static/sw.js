/* Service Worker — COP Agona Ahanta PWA
   Strategy: Cache-first for static assets, network-first for API calls. */

const CACHE_NAME   = "cop-ah-v2";
const STATIC_CACHE = "cop-ah-static-v2";

const PRECACHE = [
  "/",
  "/static/css/main.css",
  "/static/js/app.js",
  "/static/manifest.json",
  "/static/images/logo.png",
  "/static/images/default-avatar.svg",
  "/static/icons/icon-192.png",
];

// ── Install ──────────────────────────────────────────────────────────────────
self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(STATIC_CACHE).then((c) => c.addAll(PRECACHE))
  );
  self.skipWaiting();
});

// ── Activate ─────────────────────────────────────────────────────────────────
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== CACHE_NAME && k !== STATIC_CACHE)
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// ── Fetch ────────────────────────────────────────────────────────────────────
self.addEventListener("fetch", (e) => {
  const { request } = e;
  const url = new URL(request.url);

  // Always network-first for API and auth routes
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/")) {
    e.respondWith(
      fetch(request).catch(() =>
        new Response(JSON.stringify({ ok: false, error: "Offline" }), {
          headers: { "Content-Type": "application/json" },
          status: 503,
        })
      )
    );
    return;
  }

  // Cache-first for static assets
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(
      caches.match(request).then((cached) => cached || fetch(request))
    );
    return;
  }

  // Network-first with offline fallback for navigation
  e.respondWith(
    fetch(request)
      .then((res) => {
        const clone = res.clone();
        caches.open(CACHE_NAME).then((c) => c.put(request, clone));
        return res;
      })
      .catch(() => caches.match(request) || caches.match("/"))
  );
});
