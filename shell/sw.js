// 网络优先，断网时用缓存；接口不缓存。首页加载后会把目录里的全部页面预取进缓存。
const CACHE = "curryplan-v12";
const SHELL = ["/", "/app.css", "/kit.css", "/kit.js", "/ui.css", "/ui.js", "/app.js", "/push.js", "/catalog.json", "/icon-192.png"];
self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/")) return;
  // 子资源绕过浏览器的启发式缓存，改了 css/js 马上能拿到；导航请求不能带 init，照常取
  const req = e.request.mode === "navigate" ? e.request : new Request(e.request, { cache: "no-cache" });
  e.respondWith(fetch(req).then((r) => {
    if (r.ok) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
    return r;
  // 断网：先查当前版本的缓存，旧版本留下的缓存只在这里没有时才用（否则会翻出更旧的那份）
  }).catch(() => caches.open(CACHE).then((c) => c.match(e.request, { ignoreSearch: true })).then((r) => r || caches.match(e.request, { ignoreSearch: true }))));
});
// 通知：服务器发来 {title, body, url?, tag?}（见 /api/notify）。每条推送都必须弹出通知，解析不了也弹，否则浏览器会收回订阅。
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { body: e.data.text() }; }
  if (!d || typeof d !== "object") d = { body: d == null ? "" : String(d) };
  const opt = { body: d.body || "", icon: "/icon-192.png", badge: "/icon-192.png", data: { url: d.url || "/" } };
  if (d.tag) { opt.tag = d.tag; opt.renotify = true; }   // 同一个 tag 的新通知顶掉旧的，但照样提醒
  e.waitUntil(self.registration.showNotification(d.title || "curryPlan", opt));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || "/", self.location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((ws) => {
    const open = ws.find((w) => w.url === url);
    return open ? open.focus() : self.clients.openWindow(url);
  }));
});
self.addEventListener("message", (e) => {
  if (e.data && e.data.prefetch) caches.open(CACHE).then((c) => Promise.all(e.data.prefetch.map((p) => c.add(p).catch(() => null))));
});
