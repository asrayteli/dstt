const CACHE_NAME = "tobell-v1";
const STATIC_ASSETS = [
  "/static/to_bell/to_bell.css",
  "/static/to_bell/to_bell.js",
  "/static/img/favicon.ico",
  "/static/img/android-chrome-192x192.png",
  "/static/img/android-chrome-512x512.png",
  "/static/img/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(
        names.filter(function (name) { return name !== CACHE_NAME; })
             .map(function (name) { return caches.delete(name); })
      );
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  var url = new URL(event.request.url);
  if (!url.pathname.startsWith("/static/")) return;
  event.respondWith(
    caches.match(event.request).then(function (cached) {
      return cached || fetch(event.request).then(function (response) {
        if (response.ok) {
          var clone = response.clone();
          caches.open(CACHE_NAME).then(function (cache) { cache.put(event.request, clone); });
        }
        return response;
      });
    })
  );
});

self.addEventListener("push", (event) => {
  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch (error) {
      data = { title: "To Bell", body: event.data.text() };
    }
  }
  const title = data.title || "To Bell";
  const options = {
    body: data.body || "通知があります。",
    icon: data.icon || "/static/img/android-chrome-192x192.png",
    badge: data.badge || "/static/img/apple-touch-icon.png",
    tag: data.tag || data.url || "to-bell",
    renotify: true,
    data: {
      url: data.url || "/tools/to_bell",
    },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url
    ? event.notification.data.url
    : "/tools/to_bell";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client && client.url.includes("/tools/to_bell")) {
          if ("navigate" in client) {
            client.navigate(url).catch(() => {});
          }
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(url);
      }
      return undefined;
    })
  );
});
