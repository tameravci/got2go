// Bump CACHE on any change to the precache list or caching strategy.
// The activate handler deletes every other cache, which evicts the old
// poisoned 'v1' cache-first store that was serving stale HTML to users.
const CACHE = 'got2go-v2';
const PRECACHE = [
  '/',
  '/static/style.css',
  '/static/app.js',
  '/static/logo-toilet.png'
];

self.addEventListener('install', function (event) {
  // Activate this SW immediately instead of waiting for old tabs to close.
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE)
      .then(function (cache) { return cache.addAll(PRECACHE); })
      .catch(function () { /* offline at install time is fine */ })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(
          keys.filter(function (k) { return k !== CACHE; })
              .map(function (k) { return caches.delete(k); })
        );
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  var req = event.request;

  // Never intercept writes (votes, suggestions) — let them hit the network.
  if (req.method !== 'GET') return;

  var url = new URL(req.url);

  // Don't touch cross-origin requests (unpkg scripts, map tiles, etc.).
  if (url.origin !== self.location.origin) return;

  // Network-first: a fresh deploy is always picked up while online, so users
  // never get stuck on a stale page. Cache is only a last-resort offline copy.
  event.respondWith(
    fetch(req)
      .then(function (res) {
        if (res && res.ok) {
          var copy = res.clone();
          caches.open(CACHE).then(function (cache) { cache.put(req, copy); });
        }
        return res;
      })
      .catch(function () {
        // Offline: serve a cached copy. ignoreSearch lets a versioned
        // request (app.js?v=123) fall back to the precached app.js.
        return caches.match(req, { ignoreSearch: true }).then(function (cached) {
          if (cached) return cached;
          if (req.mode === 'navigate') return caches.match('/');
          return Response.error();
        });
      })
  );
});
