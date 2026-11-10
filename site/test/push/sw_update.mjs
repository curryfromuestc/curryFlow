// An old client (the service worker and page of revision OLD) gets the new version by opening the site once after the
// files changed.  env: SITE, WWW (the served directory, holding the old shell), NEW (the new shell), OLD_CACHE and
// NEW_CACHE (the cache names in the two sw.js), OLD_BELL (1 when the old page already had the notification switch).
import { connect, sleep, until, must } from "./cdp.mjs";
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
const O = process.env.SITE, OLD = process.env.OLD_CACHE, NEW = process.env.NEW_CACHE, c = await connect(9333);
const p = await c.page(O + "/");
const look = () => p.ev(`const r = await navigator.serviceWorker.getRegistration(); return { active: r && r.active && r.active.state, caches: await caches.keys(), bell: !!document.getElementById("bell") }`);
const old = await until(async () => { const s = await look(); return s.active === "activated" && s.caches.length ? s : null; }, 15000, "the old service worker");
must(OLD !== NEW, "the new sw.js names a new cache (" + OLD + " -> " + NEW + ")");
must(old.caches.join() === OLD && old.bell === (process.env.OLD_BELL === "1"), "old client: cache " + OLD + (old.bell ? ", the old switch" : ", no notification switch"), old);
execSync(`cp -r "${process.env.NEW}"/. "${process.env.WWW}"/`);
await p.go(O + "/");
const now = await until(async () => { const s = await look(); return s.caches.includes(NEW) ? s : null; }, 20000, "the new service worker");
must(now.bell, "one visit after the files changed: the page has the switch", now);
const shell = await p.ev(`const c = await caches.open("${NEW}"); const out = {}; for (const r of await c.keys()) out[new URL(r.url).pathname] = await (await c.match(r)).text(); return out`);
const fresh = (f) => readFileSync(process.env.NEW + f, "utf8");
must(shell["/push.js"] === fresh("/push.js") && shell["/"] !== undefined, "cache " + NEW + " holds the new shell (push.js as in this tree)", Object.keys(shell).sort());
await until(() => p.ev(`return document.getElementById("bell").dataset.s !== "wait"`), 15000, "the switch settles");
must(await p.ev(`return document.getElementById("bell").textContent`) === "通知：未开启", "the switch reads 未开启");
await p.shot(process.env.OUT + "/home.png");
c.close();
