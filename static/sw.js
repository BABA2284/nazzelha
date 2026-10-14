const CACHE_NAME = 'nazzelha-shell-v2';
const SHELL = ['/', '/manifest.webmanifest', '/static/icon.svg'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))));
  self.clients.claim();
});
self.addEventListener('fetch', event => {
  const req = event.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  event.respondWith(fetch(req).then(response => {
    if (response.ok && (url.pathname === '/' || url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest')) {
      const copy = response.clone(); caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
    }
    return response;
  }).catch(async () => (await caches.match(req)) || Response.error()));
});
