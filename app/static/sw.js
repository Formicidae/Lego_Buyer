/* Service worker: keeps the app usable when the phone loses the connection at the store.
   - app shell + set data: network first, cached copy as fallback
   - part images (Rebrickable CDN): cache first
   - progress polling and all writes: network only (writes are queued by app.js when they fail) */
const VERSION = 'lb-v1';
const SHELL = ['/static/app.js', '/static/style.css'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

const networkFirst = async (req) => {
  const cache = await caches.open(VERSION);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req);
    if (hit) return hit;
    throw err;
  }
};

const cacheFirst = async (req) => {
  const cache = await caches.open(VERSION);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok || res.type === 'opaque') cache.put(req, res.clone());
  return res;
};

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  if (url.hostname.endsWith('rebrickable.com')) { e.respondWith(cacheFirst(req)); return; }
  if (url.origin !== location.origin) return;

  const p = url.pathname;
  if (p.startsWith('/static/')) { e.respondWith(networkFirst(req)); return; }
  if (p === '/' || p.startsWith('/s/') && !p.includes('/checklist')) { e.respondWith(networkFirst(req)); return; }
  if (/^\/api\/sets\/[^/]+$/.test(p)) { e.respondWith(networkFirst(req)); return; }
  // everything else (progress polls, PDFs, login): straight to the network
});
