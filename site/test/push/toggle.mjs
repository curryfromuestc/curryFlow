// The switch end to end in a real Chrome: turn on (a real subscription at the browser's push service), send a test,
// send with the notify command, reload, turn off.  env: SITE, DATA (the server's ~/curry-site), HOME_FOR_NOTIFY.
import { connect, sleep, until, must } from "./cdp.mjs";
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync, rmSync } from "node:fs";
const O = process.env.SITE, DATA = process.env.DATA;
const subs = () => { try { return JSON.parse(readFileSync(DATA + "/push-subs.json", "utf8")); } catch (_) { return []; } };
const c = await connect(9333);
await c.send("Browser.grantPermissions", { origin: O, permissions: ["notifications"] });
const p = await c.page(O + "/");
const ui = () => p.ev(`const b = document.getElementById("bell"), x = document.getElementById("push"); return { bell: b.textContent, state: b.dataset.s, panel: !x.hidden, msg: x.querySelector(".msg").textContent, buttons: [...x.querySelectorAll("button")].filter((k) => !k.hidden).map((k) => k.textContent) }`);
const click = (a) => p.ev(`document.querySelector('#push [data-a="${a}"]').click()`);
const shown = () => p.ev(`const r = await navigator.serviceWorker.ready; return (await r.getNotifications()).map((n) => ({ title: n.title, body: n.body, tag: n.tag, url: n.data && n.data.url }))`);
await until(async () => (await ui()).state !== "wait", 15000, "the switch settles");
await p.ev(`document.getElementById("bell").click()`);
let u = await ui();
must(u.state === "off" && u.panel && u.buttons.join() === "开启通知", "off: the panel offers 开启通知", u);
await click("on");
u = await until(async () => { const x = await ui(); return x.msg.startsWith("已开启") || x.msg.startsWith("没开成") ? x : null; }, 45000, "turning on");
must(u.state === "on" && u.buttons.join() === "发一条测试,关闭通知", "on: subscribed at the push service and stored by the server", u.msg);
if (u.state !== "on") { c.close(); process.exit(1); }
const host = await p.ev(`const r = await navigator.serviceWorker.ready; return new URL((await r.pushManager.getSubscription()).endpoint).host`);
must(subs().length === 1 && Object.keys(subs()[0]).sort().join() === "at,auth,endpoint,p256dh,ua", "the server holds 1 subscription", "endpoint at " + host);
await click("test");
u = await until(async () => { const x = await ui(); return x.msg.startsWith("已交给") || x.msg.startsWith("没发出去") ? x : null; }, 45000, "the test send");
if (u.msg.includes("刚建的")) {   // the push service refused the subscription it had just issued; the message says to try again
  console.log("note  " + u.msg);
  await sleep(3000); await click("test"); await sleep(300);
  u = await until(async () => { const x = await ui(); return x.msg.startsWith("已交给") || x.msg.startsWith("没发出去") ? x : null; }, 45000, "the second test send");
}
must(u.msg.startsWith("已交给"), "发一条测试: the push service took it", u.msg);
let n = await until(async () => { const x = await shown(); return x.some((k) => k.tag === "push-test") ? x : null; }, 30000, "the test notification").catch(() => []);
must(n.some((k) => k.title === "curryPlan 测试通知" && k.url === "/"), "the browser decrypted it and sw.js showed it", n);
const r = spawnSync(DATA + "/notify", ["box-b 磁盘 97%", "/data 只剩 12 GB，rtlsim 回归可能写不下", "/t/p/", "--tag", "disk-box-b"], { env: { ...process.env, HOME: process.env.HOME_FOR_NOTIFY }, encoding: "utf8" });
must(r.status === 0 && r.stdout.trim() === "发出 1，失败 0", "notify command: exit 0", r.stdout.trim() + " " + r.stderr.trim());
n = await until(async () => { const x = await shown(); return x.some((k) => k.tag === "disk-box-b") ? x : null; }, 30000, "the notify notification").catch(() => []);
must(n.some((k) => k.title === "box-b 磁盘 97%" && k.body === "/data 只剩 12 GB，rtlsim 回归可能写不下" && k.url === "/t/p/"), "its title, body, url and tag arrive intact", n);
await p.go(O + "/"); await until(async () => (await ui()).state !== "wait", 15000, "the switch settles");
must((await ui()).state === "on" && subs().length === 1, "after a reload the switch still reads 已开启");

// the network fails under 发一条测试: the panel must say so, not stay at 发送中…
await p.ev(`window.__fetch = window.fetch; window.fetch = (u, o) => String(u).includes("/api/push/test") ? Promise.reject(new TypeError("Failed to fetch")) : window.__fetch(u, o)`);
await click("test");
u = await until(async () => { const x = await ui(); return x.msg !== "发送中…" ? x : null; }, 5000, "the test send to fail").catch(() => ui());
must(u.state === "on" && u.msg.startsWith("没发出去：连不上服务器") && await p.ev(`return !document.querySelector('#push [data-a="test"]').disabled`),
  "发一条测试 with the network down: says so, buttons usable again", u.msg);
await p.ev(`window.fetch = window.__fetch`);

// the push service calls the subscription gone (the stand-in answers 410 for it): the server drops it and remembers it;
// the next visit hands the old one in, is told it is gone, and the page makes a new subscription
const endpoint = () => p.ev(`const r = await navigator.serviceWorker.ready; const s = await r.pushManager.getSubscription(); return s && s.endpoint`);
const before = await endpoint();
const env = readFileSync(DATA + "/push.env", "utf8");
writeFileSync(DATA + "/push.env", `FCM_PROXY_CMD=python3 ${process.cwd()}/relay.py ${process.env.FAKE_PORT}\n`);
writeFileSync(process.env.FAKE + "/mode", "gone");
writeFileSync(DATA + "/push-subs.json", JSON.stringify(subs().map((x) => ({ ...x, at: "2026-01-01 00:00:00" }))));
const g = spawnSync(DATA + "/notify", ["失效测试"], { env: { ...process.env, HOME: process.env.HOME_FOR_NOTIFY }, encoding: "utf8" });
writeFileSync(DATA + "/push.env", env); rmSync(process.env.FAKE + "/mode");
must(g.status === 1 && g.stderr.includes("订阅已失效，已删除") && subs().length === 0, "410 for the browser's subscription: the server drops it", g.stderr.trim().split("：").slice(-2).join("："));
await p.go(O + "/"); await until(async () => (await ui()).state !== "wait", 40000, "the switch settles");
u = await ui();
const after = await endpoint(), held = subs();
must(u.state === "on" && after && after !== before && held.length === 1 && held[0].endpoint === after,
  "the next visit: the page made a new subscription, the server holds that one, the switch reads 已开启", { state: u.state, msg: u.msg, renewed: !!after && after !== before, server: held.length, same: !!held[0] && held[0].endpoint === after });
const shot = async (what) => {
  await click("test");
  let x = await until(async () => { const y = await ui(); return y.msg.startsWith("已交给") || y.msg.startsWith("没发出去") ? y : null; }, 45000, what);
  if (x.msg.includes("刚建的")) { console.log("note  " + x.msg); await sleep(3000); await click("test"); await sleep(300);
    x = await until(async () => { const y = await ui(); return y.msg.startsWith("已交给") || y.msg.startsWith("没发出去") ? y : null; }, 45000, what + " (again)"); }
  return x;
};
u = await shot("the test send on the new subscription");
must(u.msg.startsWith("已交给"), "发一条测试 on the new subscription: the push service took it", u.msg);
await p.ev(`document.getElementById("bell").click()`);
await click("off");
u = await until(async () => { const x = await ui(); return x.state === "off" ? x : null; }, 20000, "turning off");
must(subs().length === 0 && await p.ev(`const r = await navigator.serviceWorker.ready; return (await r.pushManager.getSubscription()) === null`), "off: unsubscribed in the browser and removed on the server", u.msg);
c.close();
