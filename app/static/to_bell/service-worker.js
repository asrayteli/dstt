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
          client.navigate(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
