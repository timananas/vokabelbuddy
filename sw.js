/* Vokabelbuddy Service Worker — v1
   App-Shell cache-first, /api/* IMMER Netz (bei Offline Fallback auf Cache für GET),
   POST nie cachen. */
const CACHE = 'vokabelbuddy-v42';
const SHELL = [
  '/',
  '/index.html',
  '/manifest.json',
  '/assets/app-icon-192.png',
  '/assets/app-icon-512.png',
  '/assets/apple-touch-icon.png',
  '/assets/favicon-32.png',
  '/assets/favicon-16.png',
  '/assets/logo-white.svg',
  '/assets/logo-dark.svg',
  '/assets/vokabelbuddy-wordmark.svg',
  '/assets/mascot.png',
];

self.addEventListener('install', e => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    await Promise.allSettled(SHELL.map(u => c.add(new Request(u, { cache: 'reload' }))));
    self.skipWaiting();
  })());
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys())
      if (k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;

  // Never cache POST / answer checks
  if (e.request.method !== 'GET') return;

  // API: immer frisch; Offline → letzter gecachter Stand (nur GET)
  if (url.pathname.startsWith('/api/')) {
    e.respondWith((async () => {
      try {
        const fresh = await fetch(e.request);
        if (fresh.ok && url.pathname === '/api/meta') {
          const c = await caches.open(CACHE);
          c.put(e.request, fresh.clone());
        }
        return fresh;
      } catch (err) {
        const cached = await caches.match(e.request);
        if (cached) return cached;
        return new Response(JSON.stringify({ error: 'offline' }), {
          status: 503, headers: { 'Content-Type': 'application/json' }
        });
      }
    })());
    return;
  }

  // App-Shell: stale-while-revalidate — ABER auth-Seiten (/login, /mfa, /setup, /logout,
  // /) IMMER network-first (die zeigen je Session-Status verschiedene Inhalte; ein stale
  // Cache-Eintrag verursacht sonst das kurze 'Seite nicht erreichbar'-Blitz nach dem
  // MFA-Code-POST → 303 → /):
  const AUTH_PAGES = ['/login', '/mfa', '/setup', '/logout', '/'];
  if (AUTH_PAGES.includes(url.pathname)) {
    e.respondWith((async () => {
      try {
        const fresh = await fetch(e.request);
        if (fresh && fresh.ok && url.pathname === '/') {
          caches.open(CACHE).then(c => c.put(e.request, fresh.clone()));
        }
        return fresh;
      } catch (err) {
        const cached = await caches.match(e.request);
        return cached || new Response('offline', { status: 503 });
      }
    })());
    return;
  }
  // statisch (assets): cache-first, im Hintergrund aktualisieren (stale-while-revalidate)
  e.respondWith((async () => {
    const cached = await caches.match(e.request);
    const refresh = fetch(e.request).then(res => {
      if (res && res.ok) {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
      }
      return res;
    }).catch(() => null);
    return cached || (await refresh) || new Response('offline', { status: 503 });
  })());
});