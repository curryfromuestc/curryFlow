// 网络优先，断网时用缓存；接口不缓存。首页加载后会把目录里的全部页面预取进缓存。
const CACHE = "curryplan-v11";
const SHELL = ["/", "/app.css", "/kit.css", "/kit.js", "/ui.css", "/ui.js", "/app.js", "/catalog.json", "/icon-192.png"];
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
  }).catch(() => caches.match(e.request, { ignoreSearch: true })));
});
self.addEventListener("message", (e) => {
  if (e.data && e.data.prefetch) caches.open(CACHE).then((c) => Promise.all(e.data.prefetch.map((p) => c.add(p).catch(() => null))));
});
