// キャッシュ名は「中身の世代」。戦略を変えたら必ず上げること（古い世代は activate で消える）。
const CACHE_NAME = "tobell-v2";

// オフラインでも ToBell が開けるように保持する資産。
// 画像は変化しないが CSS/JS はデプロイで変わるため、fetch 側は network-first にする。
const STATIC_ASSETS = [
  "/static/to_bell/to_bell.css",
  "/static/to_bell/to_bell.js",
  "/static/img/favicon.ico",
  "/static/img/android-chrome-192x192.png",
  "/static/img/android-chrome-512x512.png",
  "/static/img/apple-touch-icon.png",
];

// このワーカーは push 通知のために scope "/" で登録される。しかしキャッシュまで
// サイト全体に効かせると、CloudShift や PowerImager など他ツールの静的ファイルまで
// 巻き込んで固定してしまう。キャッシュ対象は ToBell が使う資産だけに限定する。
const CACHEABLE_PREFIXES = ["/static/to_bell/", "/static/img/"];

function isCacheable(pathname) {
  return CACHEABLE_PREFIXES.some(function (prefix) { return pathname.startsWith(prefix); });
}

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

// network-first + キャッシュフォールバック。
// 以前は cache-first だったため、一度取得した JS/CSS が更新されず、デプロイしても
// 古いままになっていた（PWAの「リロード」を押すまで直らない）。
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  var url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (!isCacheable(url.pathname)) return;
  event.respondWith(
    fetch(event.request)
      .then(function (response) {
        if (response && response.ok) {
          var clone = response.clone();
          caches.open(CACHE_NAME).then(function (cache) { cache.put(event.request, clone); });
        }
        return response;
      })
      .catch(function () {
        return caches.match(event.request).then(function (cached) {
          if (cached) return cached;
          return Response.error();
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

self.addEventListener("message", (event) => {
  if (!event.data || event.data.type !== "tobell-clear-caches") return;
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names.map(function (name) { return caches.delete(name); }));
    }).then(function () {
      if (event.ports && event.ports[0]) {
        try { event.ports[0].postMessage({ ok: true }); } catch (e) { /* noop */ }
      }
    })
  );
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
