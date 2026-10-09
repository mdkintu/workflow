// WorkFlow service worker (docs/02-architecture.md §4.2).
// Served by workflow.views.service_worker, which fills in VERSION (a hash of
// every precached file) and PRECACHE. A new hash installs a new cache and
// the old one is deleted on activate.
// All URLs are root-relative (CLAUDE.md "Host-agnostic").
const VERSION = "__VERSION__";
const PRECACHE = __PRECACHE__;
const SHELL = "/app/";
const OFFLINE = "/offline/";
const CACHE = "workflow-" + VERSION;
const MEDIA_CACHE = "workflow-media";
const MEDIA_LIMIT = 200;

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // `reload` skips the HTTP cache so a new version gets new files.
    await cache.addAll(PRECACHE.map((url) => new Request(url, { cache: "reload" })));
    // The shell needs a session; only a real page is kept (never a login redirect).
    try {
      const shell = await fetch(SHELL, { cache: "reload", credentials: "same-origin" });
      if (shell.ok && !shell.redirected) await cache.put(SHELL, shell);
    } catch (e) { /* installed offline: the shell is cached on the next visit */ }
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n !== CACHE && n !== MEDIA_CACHE).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/api/")) return;  // network only: sync.js handles failures
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(cacheFirst(request));
  } else if (request.mode === "navigate" && url.pathname === SHELL) {
    event.respondWith(shell(event));
  } else if (url.pathname.startsWith("/media/p/") && url.pathname.endsWith("/t")) {
    event.respondWith(thumbnail(event));
  } else if (request.mode === "navigate") {
    event.respondWith(networkOr(request, OFFLINE));
  }
});

async function cacheFirst(request) {
  const cached = await caches.match(request, { ignoreSearch: true });
  return cached || fetch(request);
}

/** The app shell from cache, refreshed in the background when online. */
async function shell(event) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(SHELL);
  const refresh = fetch(SHELL, { credentials: "same-origin", cache: "no-store" })
    .then((response) => {
      if (response.ok && !response.redirected) cache.put(SHELL, response.clone());
      return response;
    });
  if (cached) {
    event.waitUntil(refresh.catch(() => null));
    return cached;
  }
  return refresh.catch(() => caches.match(OFFLINE));
}

/** Other pages are never cached (they hold other people's data). */
async function networkOr(request, fallback) {
  try {
    return await fetch(request);
  } catch (e) {
    return (await caches.match(fallback)) || Response.error();
  }
}

/** Thumbnails: stale-while-revalidate, at most MEDIA_LIMIT kept. */
async function thumbnail(event) {
  const cache = await caches.open(MEDIA_CACHE);
  const cached = await cache.match(event.request);
  const refresh = fetch(event.request).then(async (response) => {
    if (response.ok) {
      await cache.put(event.request, response.clone());
      const keys = await cache.keys();
      await Promise.all(keys.slice(0, Math.max(0, keys.length - MEDIA_LIMIT)).map((k) => cache.delete(k)));
    }
    return response;
  });
  if (cached) {
    event.waitUntil(refresh.catch(() => null));
    return cached;
  }
  return refresh;
}
