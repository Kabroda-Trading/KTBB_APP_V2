// static/sw.js
// ==============================================================================
// PWA build (2026-11-xx) -- a minimal service worker, just enough to satisfy
// Chrome/Android's "installable" criteria (install + fetch handlers present).
// No push-subscription or notification-display logic here on purpose --
// push delivery is via Telegram, not the Web Push API, so this worker has
// nothing to do with alerts at all. No caching strategy either: every
// fetch simply passes through to the network, so the live trading data
// this site serves is never served stale from a cache.
// ==============================================================================

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
    event.respondWith(fetch(event.request));
});
