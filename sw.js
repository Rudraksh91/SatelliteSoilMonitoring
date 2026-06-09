/**
 * TERRA-CORE AgriSat — Service Worker v1.0
 * Enables offline use, home-screen installation, and fast loading.
 */

const APP_CACHE  = 'agrisat-app-v1.2';
const CDN_CACHE  = 'agrisat-cdn-v1.2';

// App shell — these files are cached immediately on install
const APP_SHELL = [
  '/SatelliteSoilMonitoring/',
  '/SatelliteSoilMonitoring/index.html',
  '/SatelliteSoilMonitoring/manifest.json',
  '/SatelliteSoilMonitoring/icons/icon.svg',
];

// CDN prefixes — cached on first use (stale-while-revalidate)
const CDN_ORIGINS = [
  'https://fonts.googleapis.com',
  'https://fonts.gstatic.com',
  'https://cdn.tailwindcss.com',
  'https://cdn.jsdelivr.net',
  'https://unpkg.com',
];

// ── Install: cache the app shell
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(APP_CACHE)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
      .catch(err => console.warn('[SW] Shell cache failed:', err))
  );
});

// ── Activate: remove old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k !== APP_CACHE && k !== CDN_CACHE)
          .map(k => {
            console.log('[SW] Deleting old cache:', k);
            return caches.delete(k);
          })
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: smart caching strategy
self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);

  // ── Skip non-GET, chrome-extension, and data URLs
  if (req.method !== 'GET') return;
  if (url.protocol === 'chrome-extension:') return;
  if (url.protocol === 'data:') return;

  // ── Skip ESP32 API calls (port 80/81) and WebSocket — never intercept
  if (url.port === '80' || url.port === '81' || url.protocol === 'ws:' || url.protocol === 'wss:') return;

  // ── Skip Open-Meteo API — always need fresh data
  if (url.hostname === 'api.open-meteo.com') return;

  // ── App shell (same origin): Network-first for HTML, cache-first for assets
  if (url.origin === self.location.origin) {
    const isNavigation = req.mode === 'navigate' || req.destination === 'document';
    if (isNavigation) {
      // HTML pages: always try network first so new deploys show immediately
      event.respondWith(
        fetch(req).then(res => {
          if (res.ok) {
            const clone = res.clone();
            caches.open(APP_CACHE).then(c => c.put(req, clone));
          }
          return res;
        }).catch(() => caches.match(req).then(cached => cached || caches.match('/SatelliteSoilMonitoring/index.html')))
      );
    } else {
      // Static assets (icons, manifest, sw.js): cache-first, update in background
      event.respondWith(
        caches.match(req).then(cached => {
          const networkFetch = fetch(req).then(res => {
            if (res.ok) caches.open(APP_CACHE).then(c => c.put(req, res.clone()));
            return res;
          }).catch(() => null);
          return cached || networkFetch;
        })
      );
    }
    return;
  }

  // ── CDN resources: Stale-while-revalidate
  if (CDN_ORIGINS.some(o => req.url.startsWith(o))) {
    event.respondWith(
      caches.open(CDN_CACHE).then(cache =>
        cache.match(req).then(cached => {
          const networkFetch = fetch(req)
            .then(res => {
              if (res.ok) cache.put(req, res.clone());
              return res;
            })
            .catch(() => null);
          // Return cached immediately, update in background
          return cached || networkFetch;
        })
      )
    );
  }
});

// ── Message handler (for app to communicate with SW)
self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (event.data === 'CLEAR_CACHE') {
    caches.keys().then(keys => keys.forEach(k => caches.delete(k)));
  }
});
