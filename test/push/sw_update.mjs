// An old client (the service worker and page from before notifications) gets the new version by opening the site once
// after the files changed.  env: SITE, WWW (the served directory, holding the old shell), NEW (the new shell).
import { connect, sleep, until, must } from "./cdp.mjs";
import { execSync } from "node:child_process";
const O = process.env.SITE, c = await connect(9333);
const p = await c.page(O + "/");
const look = () => p.ev(`const r = await navigator.serviceWorker.getRegistration(); return { active: r && r.active && r.active.state, caches: await caches.keys(), bell: !!document.getElementById("bell") }`);
const old = await until(async () => { const s = await look(); return s.active === "activated" && s.caches.length ? s : null; }, 15000, "the old service worker");
must(old.caches.join() === "curryplan-v11" && !old.bell, "old client: cache curryplan-v11, no notification switch", old);
execSync(`cp -r "${process.env.NEW}"/. "${process.env.WWW}"/`);
await p.go(O + "/");
const now = await until(async () => { const s = await look(); return s.caches.includes("curryplan-v12") ? s : null; }, 20000, "the new service worker");
must(now.bell, "one visit after the files changed: the page has the switch", now);
const shell = await p.ev(`const c = await caches.open("curryplan-v12"); return (await c.keys()).map((r) => new URL(r.url).pathname).sort()`);
must(shell.includes("/push.js") && shell.includes("/"), "cache curryplan-v12 holds the new shell", shell);
await until(() => p.ev(`return document.getElementById("bell").dataset.s !== "wait"`), 15000, "the switch settles");
must(await p.ev(`return document.getElementById("bell").textContent`) === "通知：未开启", "the switch reads 未开启");
await p.shot(process.env.OUT + "/home.png");
c.close();
