// What the switch says in each environment; that sw.js shows a notification for any payload; a click on one; and that
// the cached shell still opens with the site down.  env: SITE, OUT, NGINX_PID.
import { connect, sleep, until, must } from "./cdp.mjs";
import { execSync } from "node:child_process";
const O = process.env.SITE;
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1";
const CRIOS = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0.6778.73 Mobile/15E148 Safari/604.1";
const PIXEL = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36";
const STANDALONE = `Object.defineProperty(Navigator.prototype, "standalone", { get: () => true });`;
const fakeKey = (body, status) => `const f = window.fetch; window.fetch = (u, o) => String(u).includes("/api/push/key") ? Promise.resolve(new Response(${JSON.stringify(body)}, { status: ${status} })) : f(u, o);`;
const c = await connect(9333);
async function look(name, { ua, platform, inject, perm }, bell, msg, buttons, shot) {
  await c.send("Browser.resetPermissions", {});
  if (perm) await c.send("Browser.setPermission", { origin: O, permission: { name: "notifications" }, setting: perm });
  const p = await c.page();
  if (ua) await p.send("Emulation.setUserAgentOverride", { userAgent: ua, platform });
  if (ua) await p.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 600, deviceScaleFactor: 2, mobile: true });
  if (inject) await p.send("Page.addScriptToEvaluateOnNewDocument", { source: inject });
  await p.go(O + "/");
  await until(() => p.ev(`return document.getElementById("bell").dataset.s !== "wait"`), 15000, name);
  const u = await p.ev(`const b = document.getElementById("bell"), x = document.getElementById("push"); b.click(); return { bell: b.textContent, msg: x.querySelector(".msg").textContent, buttons: [...x.querySelectorAll("button")].filter((k) => !k.hidden).map((k) => k.textContent).join() }`);
  must(u.bell === "通知：" + bell && u.msg.includes(msg) && u.buttons === buttons, name, u.bell + " | " + u.msg);
  if (shot) await p.shot(process.env.OUT + "/" + shot);
  await c.send("Target.closeTarget", { targetId: p.targetId });
}
await look("iPhone, Safari tab", { ua: IPHONE, platform: "iPhone" }, "先加到主屏幕", "添加到主屏幕", "", "iphone-tab.png");
await look("iPhone, Chrome tab", { ua: CRIOS, platform: "iPhone" }, "先加到主屏幕", "添加到主屏幕", "");
await look("iPhone, home-screen app", { ua: IPHONE, platform: "iPhone", inject: STANDALONE, perm: "prompt" }, "未开启", "开启后", "开启通知");
await look("iPhone, home-screen app before iOS 16.4 (no PushManager)", { ua: IPHONE, platform: "iPhone", inject: STANDALONE + "delete window.PushManager;" }, "不可用", "iOS 16.4", "");
await look("Pixel, Chrome tab", { ua: PIXEL, platform: "Linux armv81", perm: "prompt" }, "未开启", "开启后", "开启通知", "pixel-tab.png");
await look("an in-app browser (no PushManager)", { ua: PIXEL + " MicroMessenger/8.0", platform: "Linux armv81", inject: "delete window.PushManager;" }, "不可用", "不支持网页推送", "");
await look("permission denied", { ua: PIXEL, platform: "Linux armv81", perm: "denied" }, "权限被拒绝", "网站设置", "");
await look("the server's push module is down (503)", { inject: fakeKey(JSON.stringify({ error: "推送模块没起来（ModuleNotFoundError）" }), 503), perm: "prompt" }, "不可用", "推送模块没起来", "");
await look("the login expired (the API answers with the login page)", { inject: fakeKey("<!doctype html><title>curryPlan</title>", 200), perm: "prompt" }, "不可用", "登录过期", "");

// sw.js on whatever arrives: Chrome hands these payloads straight to the service worker
await c.send("Browser.resetPermissions", {});
await c.send("Browser.grantPermissions", { origin: O, permissions: ["notifications"] });
const p = await c.page(O + "/");
await p.send("ServiceWorker.enable");
await sleep(1000);
const reg = c.events.filter((e) => e.method === "ServiceWorker.workerRegistrationUpdated").flatMap((e) => e.params.registrations).find((r) => r.scopeURL.startsWith(O));
const shown = () => p.ev(`const r = await navigator.serviceWorker.ready; return (await r.getNotifications()).map((n) => ({ title: n.title, body: n.body, tag: n.tag, url: n.data && n.data.url }))`);
const clear = () => p.ev(`const r = await navigator.serviceWorker.ready; for (const n of await r.getNotifications()) n.close()`);
for (const [name, data, want] of [["a payload that is not JSON", "纯文本告警", { title: "curryPlan", body: "纯文本告警", tag: "", url: "/" }], ["an empty payload", "", { title: "curryPlan", body: "", tag: "", url: "/" }], ["JSON that is not an object (null)", "null", { title: "curryPlan", body: "", tag: "", url: "/" }],
  ["JSON that is a bare string", JSON.stringify("磁盘满了"), { title: "curryPlan", body: "磁盘满了", tag: "", url: "/" }],
  ["JSON with only a title", JSON.stringify({ title: "只有标题" }), { title: "只有标题", body: "", tag: "", url: "/" }],
  ["title, body, url, tag", JSON.stringify({ title: "作业完成", body: "rtl-core 通过", url: "/t/p/", tag: "job-1" }), { title: "作业完成", body: "rtl-core 通过", tag: "job-1", url: "/t/p/" }],
  ["a url on another site (refused: the click opens /)", JSON.stringify({ title: "外链", body: "看这里", url: "https://evil.example/x" }), { title: "外链", body: "看这里\n（附带的链接不是本站地址，已忽略）", tag: "", url: "/" }],
  ["a protocol-relative url on another site", JSON.stringify({ title: "外链", url: "//evil.example/x" }), { title: "外链", body: "（附带的链接不是本站地址，已忽略）", tag: "", url: "/" }],
  ["a javascript: url", JSON.stringify({ title: "外链", url: "javascript:alert(1)" }), { title: "外链", body: "（附带的链接不是本站地址，已忽略）", tag: "", url: "/" }]]) {
  await clear();
  await p.send("ServiceWorker.deliverPushMessage", { origin: O, registrationId: reg.registrationId, data });
  const n = await until(async () => { const x = await shown(); return x.length ? x : null; }, 5000, name).catch(() => []);
  must(JSON.stringify(n) === JSON.stringify([want]), "sw.js shows a notification for " + name, n);
}
// a click: headless Chrome has no notification to click, so the same event is dispatched inside the worker; without a
// real click the browser refuses openWindow, which is as far as this can go
const swt = (await c.send("Target.getTargets")).targetInfos.find((t) => t.type === "service_worker" && t.url.startsWith(O));
const { sessionId } = await c.send("Target.attachToTarget", { targetId: swt.targetId, flatten: true });
const r = await c.send("Runtime.evaluate", { awaitPromise: true, returnByValue: true, expression: `(async () => { const n = (await self.registration.getNotifications())[0]; let res = "handler did not call waitUntil"; const ev = new NotificationEvent("notificationclick", { notification: n }); const w = ev.waitUntil.bind(ev); ev.waitUntil = (pr) => { res = pr.then(() => "opened", (e) => e.message); w(pr); }; self.dispatchEvent(ev); return { handler: await res, left: (await self.registration.getNotifications()).length }; })()` }, sessionId);
must(r.result.value.left === 0 && /opened|focused|Not allowed to (open|focus) a window/.test(r.result.value.handler), "notificationclick closes the notification and goes on to open its url", r.result.value);
// what the click opens, with openWindow stubbed inside the worker: a url on this site as given, one elsewhere (a
// notification an older sw.js put up) the home page instead
const opens = async (url) => (await c.send("Runtime.evaluate", { awaitPromise: true, returnByValue: true, expression: `(async () => {
  for (const n of await self.registration.getNotifications()) n.close();
  await self.registration.showNotification("点我", { data: { url: ${JSON.stringify(url)} } });
  const n = (await self.registration.getNotifications())[0]; let got = null;
  const ow = self.clients.openWindow, ma = self.clients.matchAll;
  self.clients.openWindow = (u) => { got = u; return Promise.resolve(null); }; self.clients.matchAll = () => Promise.resolve([]);
  const ev = new NotificationEvent("notificationclick", { notification: n }); let p = Promise.resolve(); const w = ev.waitUntil.bind(ev); ev.waitUntil = (x) => { p = x; w(x); };
  self.dispatchEvent(ev); await p.catch(() => null); self.clients.openWindow = ow; self.clients.matchAll = ma; return got; })()` }, sessionId)).result.value;
for (const [url, want] of [["/t/p/", O + "/t/p/"], ["https://evil.example/x", O + "/"], ["//evil.example/x", O + "/"], ["javascript:alert(1)", O + "/"]])
  { const got = await opens(url); must(got === want, "a click on a notification with url " + url + " opens " + want.replace(O, ""), got); }
// Android draws the badge as a one-colour mask, so the colour icon became a white square: no badge
await clear();
await p.send("ServiceWorker.deliverPushMessage", { origin: O, registrationId: reg.registrationId, data: JSON.stringify({ title: "徽标" }) });
const badge = await until(async () => { const x = await p.ev(`const r = await navigator.serviceWorker.ready; return (await r.getNotifications()).map((n) => ({ badge: n.badge, icon: n.icon }))`); return x.length ? x[0] : null; }, 5000, "a notification").catch(() => ({}));
must(badge.badge === "" && badge.icon === O + "/icon-192.png", "notifications carry the icon and no badge", badge);
// the site goes away: the shell and a prefetched page still open from the cache
execSync(`kill ${process.env.NGINX_PID}`); await sleep(500);
await p.go(O + "/"); await sleep(1500);
const home = await p.ev(`return { title: document.title, rows: document.querySelectorAll("#list .row").length, bell: !!document.getElementById("bell") }`);
must(home.title === "curryPlan" && home.rows === 1 && home.bell, "site down: the home page opens from the cache", home);
await p.go(O + "/t/p/"); await sleep(800);
must(await p.ev(`return document.body.innerText.trim()`) === "页面正文", "site down: a prefetched page opens from the cache");
c.close();
