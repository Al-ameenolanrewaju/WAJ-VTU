const CACHE_NAME = 'waj-vtu-v2';
const ASSETS = [
  '/',
  '/static/manifest.webmanifest',
  '/static/images/waj-vtu-logo.jpeg',
  '/static/images/waj-vtu-promo.jpeg'
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return Promise.allSettled(
        ASSETS.map((url) =>
          cache.add(url).catch((err) => {
            console.warn('[sw] Pre-cache skipped for:', url, err);
          })
        )
      );
    })
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  // 1. Never intercept external third-party requests (Google Fonts, CDNs, Analytics)
  if (url.origin !== self.location.origin) {
    return;
  }

  // 2. Never intercept API calls, dynamic pages, admin dashboard, or auth endpoints
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/admin') ||
    url.pathname.startsWith('/auth/') ||
    url.pathname.startsWith('/buy/')
  ) {
    return;
  }

  // 3. For same-origin static assets: cache-first with network fallback
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          if (!response || response.status !== 200 || response.type !== 'basic') {
            return response;
          }
          const cloned = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, cloned));
          return response;
        });
      })
    );
    return;
  }

  // 4. For page navigations: network first with offline fallback to cached home
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => caches.match('/'))
    );
  }
});


// --- WEB PUSH NOTIFICATION HANDLERS ---
self.addEventListener('push', (event) => {
  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data = { title: 'WAJ VTU Deal Alert 🚀', body: event.data.text() };
    }
  }

  const title = data.title || 'WAJ VTU Deal Alert 🚀';
  const options = {
    body: data.body || 'New data deals are live! Tap to check them out.',
    icon: data.icon || '/static/images/waj-vtu-logo.jpeg',
    badge: '/static/images/waj-vtu-logo.jpeg',
    data: {
      url: data.url || '/'
    },
    vibrate: [100, 50, 100],
    actions: [
      { action: 'open', title: 'Open WAJ VTU 📱' }
    ]
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url === targetUrl && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
